# Sublime Text 3 plugin for LLDB integration

## Features

- Setting breakpoints
- Running with connected stdin/out/err in output panel
- LLDB debug prompt
- Local variable display
- Backtraces

## Roadmap

- Stabilize killing of debug server
- Work out bugs in lldb console show/hide
- Remote debugging

## Setup

Use the default Sublime method of overriding configuration from the menu.
Available configuration options:

- `lldb_python_path` path to lldb python package directory to use for the debugger
- `auto_show_lldb_console` boolean, automatically show the lldb console and backtrace windows when starting the debugger

## How to use

To use the debugger you have to configure a debug target and its settings.
To keep it with the project we save the settings to the sublime project file.

Example content of `Project.sublime-project`:

```
{
	"folders": [
		{
			"path": ".",
		}
	]
	"settings": {
		"SublimeAnarchyDebug": {
			"debug": {
				"executable": "${project_path}/bin/executable",
				"params": [
				],
				"path": [
				],
				"environment": [
				],
				"working_dir": "${project_path}"
			}
		}
	}
}
```

Put that into your project root and use the menu entry `Project->Open Project...` to open the project (or double-click in your filesystem browser or even open with `subl <ProjectFile>` from the command line.)

If the project is open just use the Command Palette to execute some Debug commands (all prefixed with `AnarchyDebug:`).
