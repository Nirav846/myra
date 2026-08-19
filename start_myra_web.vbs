' MYRA Desktop Launcher
' Starts all MYRA services silently via launch_myra.py
' Double-click to start, Ctrl+C in a terminal to stop

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "D:\01screener\Myra"
WshShell.Run "cmd /k ""D:\01screener\Myra\pkscreener_env\Scripts\python.exe"" -OO launch_myra.py", 1, False
