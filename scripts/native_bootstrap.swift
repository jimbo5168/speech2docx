import AppKit
import CryptoKit
import Darwin

struct Release: Decodable {
    struct Entry: Decodable { let path: String; let size: Int64; let sha256: String }
    let files: [Entry]
    let repositories: [String: String]?
}
struct SetupError: Error, CustomStringConvertible { let description: String }
struct CommandError: Error, CustomStringConvertible {
    let status: Int32
    let description: String
}

final class Bootstrap: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let fm = FileManager.default
    let resources = Bundle.main.resourceURL!
    var home: URL!
    var runtime: URL!
    var entry: Release.Entry!
    var runtimeSources: [String] = []
    var window: NSWindow!
    let label = NSTextField(wrappingLabelWithString: "")
    let detail = NSTextField(wrappingLabelWithString: "")
    let bar = NSProgressIndicator()
    let button = NSButton(title: "开始下载", target: nil, action: nil)
    let stateLock = NSLock()
    var paused = false
    var busy = false
    var child: Process?
    var phase = ""
    var part: URL?
    var previousBytes: Int64 = 0
    var previousTime = Date()
    var lockFD: Int32 = -1

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let env = ProcessInfo.processInfo.environment
            let userHome = env["HOME"] ?? NSHomeDirectory()
            home = URL(fileURLWithPath: env["ASR_HOME"] ?? userHome + "/Library/Application Support/为我闻思")
            try fm.createDirectory(at: home.appendingPathComponent("work"), withIntermediateDirectories: true)
            lockFD = open(home.appendingPathComponent("work/native-bootstrap.lock").path, O_CREAT | O_RDWR, 0o600)
            guard lockFD >= 0, flock(lockFD, LOCK_EX | LOCK_NB) == 0 else {
                throw SetupError(description: "另一个窗口正在准备运行环境，请稍后重试。")
            }
            let release = try JSONDecoder().decode(Release.self, from: Data(contentsOf: resources.appendingPathComponent("runtime_manifest.json")))
            guard release.files.count == 1 else { throw SetupError(description: "运行环境清单无效") }
            entry = release.files[0]
            let repos = release.repositories ?? [:]
            runtimeSources = [
                "https://huggingface.co/" + (repos["Hugging Face"] ?? "jimbo5168/weiwowensi-asr-r3-8bit") + "/resolve/main/",
                "https://modelscope.cn/models/" + (repos["ModelScope"] ?? "jimbo5168/weiwowensi-asr-r3-8bit") + "/resolve/master/"
            ]
            runtime = home.appendingPathComponent("runtimes/" + entry.sha256)
            makeWindow()
            Timer.scheduledTimer(withTimeInterval: 0.3, repeats: true) { _ in self.refresh() }
            if fm.fileExists(atPath: runtime.appendingPathComponent(".complete").path) || env["ASR_BOOTSTRAP_AUTOSTART"] == "1" { start() }
        } catch { showError(error) }
    }
    func makeWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 580, height: 340), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "为我闻思 · 首次准备"
        window.delegate = self
        window.backgroundColor = NSColor(srgbRed: 0.16, green: 0.055, blue: 0.075, alpha: 1)
        window.center()
        let gold = NSColor(srgbRed: 0.84, green: 0.69, blue: 0.40, alpha: 1)
        let title = NSTextField(labelWithString: "为我闻思")
        title.font = .systemFont(ofSize: 28, weight: .medium)
        title.textColor = gold
        title.frame = NSRect(x: 32, y: 268, width: 510, height: 40)
        label.textColor = gold
        label.font = .systemFont(ofSize: 16)
        label.frame = NSRect(x: 32, y: 190, width: 510, height: 65)
        let model = try? JSONDecoder().decode(Release.self, from: Data(contentsOf: resources.appendingPathComponent("app/src/model_manifest.json")))
        let total = entry.size + (model?.files.reduce(0) { $0 + $1.size } ?? 0)
        label.stringValue = String(format: "首次下载运行环境和中文模型，共 %.2f GB", Double(total) / 1e9)
        detail.textColor = .white
        detail.font = .systemFont(ofSize: 13)
        detail.frame = NSRect(x: 32, y: 108, width: 510, height: 70)
        detail.stringValue = "仅首次下载，无需登录。\n50 / 100 / 300 Mbps 预计约 \(Int(ceil(Double(total) * 8 / 50e6 / 60))) / \(Int(ceil(Double(total) * 8 / 100e6 / 60))) / \(Int(ceil(Double(total) * 8 / 300e6 / 60))) 分钟。\n可暂停，重新打开后继续下载。"
        bar.frame = NSRect(x: 32, y: 88, width: 510, height: 12)
        bar.isIndeterminate = false
        bar.maxValue = 100
        button.frame = NSRect(x: 400, y: 26, width: 145, height: 36)
        button.bezelStyle = .rounded
        button.contentTintColor = gold
        button.target = self
        button.action = #selector(toggle)
        for view in [title, label, detail, bar, button] { window.contentView!.addSubview(view) }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc func toggle() {
        stateLock.lock()
        if busy {
            paused = true
            child?.terminate()
            stateLock.unlock()
            button.isEnabled = false
        } else {
            stateLock.unlock()
            start()
        }
    }
    func start() {
        stateLock.lock(); paused = false; busy = true; stateLock.unlock()
        button.title = "暂停"
        DispatchQueue.global().async {
            do {
                try self.installRuntime()
                try self.checkPaused()
                self.phase = "model"
                try? self.fm.removeItem(at: self.home.appendingPathComponent("work/bootstrap-progress.json"))
                try self.run(self.runtime.appendingPathComponent("bin/python3").path, [self.resources.appendingPathComponent("app/src/bootstrap_model.py").path], environment: self.environment())
                try self.checkPaused()
                DispatchQueue.main.async {
                    self.window.orderOut(nil)
                    NSApp.setActivationPolicy(.accessory)
                }
                self.phase = "gui"
                try self.run(self.runtime.appendingPathComponent("bin/python3").path, [self.resources.appendingPathComponent("app/src/gui.py").path] + Array(CommandLine.arguments.dropFirst()), environment: self.environment())
                DispatchQueue.main.async { NSApp.terminate(nil) }
            } catch {
                self.stateLock.lock(); let stopped = self.paused; self.busy = false; self.stateLock.unlock()
                DispatchQueue.main.async {
                    self.window.makeKeyAndOrderFront(nil)
                    self.detail.stringValue = stopped ? "已暂停，下载进度已保存。" : String(describing: error)
                    self.button.title = "继续 / 重试"
                    self.button.isEnabled = true
                }
            }
        }
    }
    func checkPaused() throws {
        stateLock.lock(); let value = paused; stateLock.unlock()
        if value { throw SetupError(description: "已暂停") }
    }
    func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["ASR_HOME"] = home.path
        env["PATH"] = runtime.appendingPathComponent("bin").path + ":/usr/bin:/bin:/usr/sbin:/sbin"
        env["FFMPEG"] = runtime.appendingPathComponent("bin/ffmpeg").path
        env["FFPROBE"] = runtime.appendingPathComponent("bin/ffprobe").path
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONHOME"] = nil
        env["PYTHONPATH"] = nil
        return env
    }
    func run(_ executable: String, _ arguments: [String], environment: [String: String]? = nil) throws {
        try checkPaused()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.environment = environment
        let log = home.appendingPathComponent("work/bootstrap.log")
        if !fm.fileExists(atPath: log.path) { fm.createFile(atPath: log.path, contents: nil) }
        let handle = try FileHandle(forWritingTo: log)
        defer { try? handle.close() }
        try handle.seekToEnd()
        process.standardOutput = handle
        process.standardError = handle
        stateLock.lock()
        if paused { stateLock.unlock(); throw SetupError(description: "已暂停") }
        do { try process.run() } catch { stateLock.unlock(); throw error }
        child = process
        stateLock.unlock()
        process.waitUntilExit()
        stateLock.lock(); child = nil; stateLock.unlock()
        guard process.terminationStatus == 0 else { throw CommandError(status: process.terminationStatus, description: "准备失败，请重试。详情见 \(log.path)") }
    }
    func digest(_ url: URL) throws -> String {
        let input = try FileHandle(forReadingFrom: url)
        defer { try? input.close() }
        var hash = SHA256()
        while let data = try input.read(upToCount: 1024 * 1024), !data.isEmpty {
            try checkPaused()
            hash.update(data: data)
        }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
    func installRuntime() throws {
        if fm.fileExists(atPath: runtime.appendingPathComponent(".complete").path) { return }
        phase = "runtime"
        let archives = home.appendingPathComponent("runtime-archives")
        try fm.createDirectory(at: archives, withIntermediateDirectories: true)
        let archive = archives.appendingPathComponent(entry.sha256 + ".tar.gz")
        part = archives.appendingPathComponent(entry.sha256 + ".part")
        let sources = ProcessInfo.processInfo.environment["ASR_RUNTIME_SOURCES"].flatMap { $0.data(using: .utf8) }.flatMap { try? JSONDecoder().decode([String].self, from: $0) } ?? runtimeSources
        if try !fm.fileExists(atPath: archive.path) || digest(archive) != entry.sha256 {
            var verified = false
            if fileSize(part!) == entry.size {
                if try digest(part!) == entry.sha256 {
                    try? fm.removeItem(at: archive)
                    try fm.moveItem(at: part!, to: archive)
                    verified = true
                } else {
                    try fm.removeItem(at: part!)
                }
            }
            for base in sources where !verified {
                for _ in 0..<2 {
                    do {
                        try run("/usr/bin/curl", ["--fail", "--location", "--connect-timeout", "15", "--speed-limit", "1024", "--speed-time", "30", "--continue-at", "-", "--output", part!.path, base + entry.path])
                        guard fileSize(part!) == entry.size, try digest(part!) == entry.sha256 else {
                            try? fm.removeItem(at: part!)
                            throw SetupError(description: "运行环境校验失败")
                        }
                        try? fm.removeItem(at: archive)
                        try fm.moveItem(at: part!, to: archive)
                        verified = true
                        break
                    } catch {
                        try checkPaused()
                        // curl reports 33 when the server ignores a Range request.
                        if fileSize(part!) > entry.size || (error as? CommandError)?.status == 33 { try? fm.removeItem(at: part!) }
                    }
                }
                if verified { break }
            }
            guard verified else { throw SetupError(description: "运行环境下载失败，请检查网络后重试。") }
        }
        phase = "extract"
        let staging = home.appendingPathComponent("runtimes/" + entry.sha256 + ".staging")
        try? fm.removeItem(at: staging)
        try fm.createDirectory(at: staging, withIntermediateDirectories: true)
        // Only the digest-pinned archive can reach the system extractor.
        try run("/usr/bin/tar", ["-xzf", archive.path, "-C", staging.path])
        guard fm.isExecutableFile(atPath: staging.appendingPathComponent("bin/python3").path) else { throw SetupError(description: "运行环境缺少 Python") }
        try Data(entry.sha256.utf8).write(to: staging.appendingPathComponent(".complete"), options: .atomic)
        try? fm.removeItem(at: runtime)
        try fm.moveItem(at: staging, to: runtime)
    }
    func fileSize(_ url: URL) -> Int64 { (try? fm.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.int64Value ?? 0 }
    func refresh() {
        stateLock.lock(); let active = busy && !paused; stateLock.unlock()
        guard active else { return }
        if phase == "runtime", let url = part {
            let bytes = fileSize(url)
            let now = Date()
            let speed = max(0, Double(bytes - previousBytes) / max(0.01, now.timeIntervalSince(previousTime)))
            previousBytes = bytes; previousTime = now
            label.stringValue = "正在下载运行环境"
            bar.doubleValue = Double(bytes) / Double(entry.size) * 100
            detail.stringValue = String(format: "%.1f%% · %.1f / %.1f MB\n%.1f MB/s · 预计剩余 %.0f 秒", bar.doubleValue, Double(bytes)/1e6, Double(entry.size)/1e6, speed/1e6, Double(max(0, entry.size-bytes))/max(1,speed))
        } else if phase == "extract" {
            label.stringValue = "正在校验并安装运行环境"
        } else if phase == "model" {
            label.stringValue = "正在准备中文模型"
            if let data = try? Data(contentsOf: home.appendingPathComponent("work/bootstrap-progress.json")), let state = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                let bytes = (state["downloaded"] as? NSNumber)?.doubleValue ?? 0
                let total = (state["total"] as? NSNumber)?.doubleValue ?? 1
                let speed = (state["speed"] as? NSNumber)?.doubleValue ?? 0
                bar.doubleValue = bytes / total * 100
                detail.stringValue = String(format: "%.1f%% · %.2f / %.2f GB\n%.1f MB/s · 预计剩余 %.0f 秒\n%@", bar.doubleValue, bytes/1e9, total/1e9, speed/1e6, (total-bytes)/max(1,speed), state["status"] as? String ?? "")
            }
        }
    }
    func showError(_ error: Error) {
        let alert = NSAlert()
        alert.messageText = "为我闻思"
        alert.informativeText = String(describing: error)
        alert.runModal()
        NSApp.terminate(nil)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func windowWillClose(_ notification: Notification) { NSApp.terminate(nil) }
    func applicationWillTerminate(_ notification: Notification) { child?.terminate() }
}
let application = NSApplication.shared
let delegate = Bootstrap()
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
