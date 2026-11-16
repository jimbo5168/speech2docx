/* Native entry point so the process belongs to 为我闻思.app, not Python.app. */
#include <Python.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

static int add_venv_site(void) {
    const char *home = getenv("ASR_HOME");
    if (home == NULL || home[0] == '\0') {
        return 0;
    }
    char cmd[PATH_MAX + 256];
    int n = snprintf(
        cmd,
        sizeof(cmd),
        "import site, os\n"
        "home = os.environ.get('ASR_HOME', '')\n"
        "path = os.path.join(home, '.venv', 'lib', 'python%d.%d', 'site-packages')\n"
        "if os.path.isdir(path):\n"
        "    site.addsitedir(path)\n",
        PY_MAJOR_VERSION,
        PY_MINOR_VERSION);
    if (n < 0 || (size_t)n >= sizeof(cmd)) {
        return -1;
    }
    return PyRun_SimpleString(cmd);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s script.py [args...]\n", argv[0]);
        return 2;
    }

    /* Direct CLI launches need the same runtime home as gui-launch. */
    const char *asr_home = getenv("ASR_HOME");
    if (asr_home == NULL || asr_home[0] == '\0') {
        const char *user_home = getenv("HOME");
        char default_home[PATH_MAX];
        if (user_home == NULL || snprintf(default_home, sizeof(default_home), "%s/ASR", user_home) >= sizeof(default_home)) {
            fprintf(stderr, "Cannot determine ASR_HOME\n");
            return 1;
        }
        setenv("ASR_HOME", default_home, 1);
    }

    const char *home = getenv("ASR_HOME");
    if (home && home[0]) {
        char venv[PATH_MAX];
        snprintf(venv, sizeof(venv), "%s/.venv", home);
        setenv("VIRTUAL_ENV", venv, 0);
    }

    /* Python libraries spawn helpers using sys.executable -c/-m. */
    if (strcmp(argv[1], "-c") == 0 || strcmp(argv[1], "-m") == 0) {
        char python[PATH_MAX];
        int length = snprintf(python, sizeof(python), "%s/.venv/bin/python", home);
        if (length < 0 || (size_t)length >= sizeof(python)) {
            return 1;
        }
        argv[0] = python;
        execv(python, argv);
        perror("Cannot launch Python helper");
        return 1;
    }

    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.parse_argv = 0;
    status = PyConfig_SetBytesString(&config, &config.program_name, argv[0]);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        Py_ExitStatusException(status);
    }
    /* sys.argv is the script and its arguments, like a normal python invocation. */
    status = PyConfig_SetBytesArgv(&config, argc - 1, argv + 1);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        Py_ExitStatusException(status);
    }

    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }

    if (add_venv_site() != 0) {
        PyErr_Print();
        Py_Finalize();
        return 1;
    }

    FILE *fp = fopen(argv[1], "r");
    if (fp == NULL) {
        fprintf(stderr, "cannot open %s\n", argv[1]);
        Py_Finalize();
        return 1;
    }
    int rc = PyRun_SimpleFileEx(fp, argv[1], 1);
    if (Py_FinalizeEx() < 0) {
        return 120;
    }
    return rc != 0;
}
