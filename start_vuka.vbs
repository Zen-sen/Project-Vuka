Set WshShell = CreateObject("WScript.Shell")
' Start Kronos in its own window
WshShell.Run "cmd.exe /c ""C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe kronos_server.py""", 1, False
' Wait for model to load
WScript.Sleep 20000
' Start Supervisor
WshShell.Run "cmd.exe /c start /MIN ""Vuka Supervisor"" ""C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe"" supervisor.py", 0, False
WScript.Sleep 3000
' Start Dashboard
WshShell.Run "cmd.exe /c start /MIN ""Vuka Dashboard"" ""C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe"" dashboard.py", 0, False
