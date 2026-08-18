' MYRA Desktop Launcher
' Starts all MYRA services silently via launch_myra.py
' Double-click to start, Ctrl+C in a terminal to stop

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "D:\01screener\Myra"
WshShell.Run "python -OO launch_myra.py", 0, False
