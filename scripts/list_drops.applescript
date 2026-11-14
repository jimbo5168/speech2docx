tell application "Finder"
  if not (exists folder "语音转文字" of desktop) then return ""
  set root to folder "语音转文字" of desktop
  set out to {}
  set folderNames to {"中文", "藏文"}
  repeat with i from 1 to count of folderNames
    set sName to item i of folderNames
    if exists folder sName of root then
      set subF to folder sName of root
      set fileCount to count files of subF
      if fileCount is greater than 0 then
        repeat with j from 1 to fileCount
          try
            set f to file j of subF
            set p to POSIX path of (f as alias)
            set n to name of f
            set sz to size of f
            set end of out to sName & tab & p & tab & n & tab & (sz as string)
          end try
        end repeat
      end if
    end if
  end repeat
  set AppleScript's text item delimiters to linefeed
  return out as text
end tell
