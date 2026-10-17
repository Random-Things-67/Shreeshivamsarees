# Saree Stock Manager V12

A simple, locally hosted inventory management application for managing saree stock through a web browser.

## Features

- Manage saree inventory and stock records
- Access the application through a web browser
- Run locally on your own computer or Android device
- Keep inventory data in a local SQLite database
- Support for Linux, Windows, macOS and Android using Termux

## Requirements

- Python 3
- A terminal or command prompt
- A modern web browser
- The project source files

## 1. Android (Termux)

1. Install Termux from a trusted source.
2. Open Termux and update packages:

```bash
pkg update && pkg upgrade
```

3. Install Python and Git:

```bash
pkg install python git
```

4. Enter the project directory. For example:

```bash
cd ~/SareeStockManager
```

Replace this path with the actual directory containing the project.

5. Install dependencies if the project includes a `requirements.txt` file:

```bash
pip install -r requirements.txt
```

6. Start the application:

```bash
python server.py
```

If the project provides a working `start.sh` script, you can use it instead:

```bash
bash start.sh
```

Open the local address shown in the terminal in your Android browser. It is commonly `http://127.0.0.1:5000`, but use the actual address and port printed by the application.

## 2. Linux

1. Install Python 3, pip and Git using your distribution's package manager.
2. Open a terminal in the project directory.
3. Install dependencies if `requirements.txt` exists:

```bash
python3 -m pip install -r requirements.txt
```

4. Start the application:

```bash
python3 server.py
```

Alternatively, if `start.sh` is configured for your system:

```bash
bash start.sh
```

5. Open the local URL shown in the terminal in your browser.

## 3. Windows

1. Install Python 3 from [python.org](https://www.python.org/downloads/). Enable the option to add Python to PATH during installation.
2. Open Command Prompt or PowerShell in the project folder.
3. If `requirements.txt` exists, install dependencies:

```powershell
py -m pip install -r requirements.txt
```

4. Start the application:

```powershell
py server.py
```

5. Open the local URL printed in the terminal in your browser.

Note: `start.sh` is a shell script and does not run natively in ordinary Windows Command Prompt. Use the Python command above or a compatible environment such as WSL.

## 4. macOS

1. Install Python 3 from [python.org](https://www.python.org/downloads/) if it is not already installed.
2. Open Terminal and navigate to the project directory.
3. If `requirements.txt` exists, install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

4. Start the application:

```bash
python3 server.py
```

Alternatively, if `start.sh` is compatible with your system:

```bash
bash start.sh
```

5. Open the local URL printed in the terminal in your browser.

## Database and Backups

- The application uses a local SQLite database.
- Keep regular backups of your database file.
- Do not delete or replace an existing database when updating the application unless you have a verified backup.
- Database filenames and locations depend on the application configuration.
- Uploaded images and other local data may be stored separately from the source code.

## Updating the Application

1. Back up your database and uploaded images.
2. Keep a backup of the current application files.
3. Replace or update the source files carefully.
4. Install any newly required dependencies.
5. Restart the application and verify that inventory records and images remain available.

## Troubleshooting

**Python command not found**

Install Python 3 and ensure it is available in your terminal's PATH.

**Module not found**

If a `requirements.txt` file exists, install its dependencies using the instructions for your operating system.

**Address already in use**

Another process may already be using the application's port. Stop that process or configure a different port if the application supports it.

**Cannot open the application**

Make sure the server is running and use the exact local URL and port shown in the terminal.

**Database or image missing**

Check the application's configured database and upload paths. Restore backups if necessary.

## Security Notes

- Keep the database and uploaded files backed up.
- Do not expose the application to the public internet without appropriate security protections.
- Use trusted sources for software downloads.
- Never publish passwords, API keys or other secrets in a public repository.

## Important

These instructions assume the project can run with Python and that `server.py` is its entry point. Exact dependencies, ports, network access and platform compatibility depend on the implementation of Saree Stock Manager V12.
