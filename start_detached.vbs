Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe kronos_server.py", 1, False
WScript.Sleep 15000
WshShell.Run "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe supervisor.py", 7, False
WScript.Sleep 3000
WshShell.Run "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe dashboard.py", 7, False
