# FDFTP
This repository implements a little tool of FTP by python using socket module for FDU Computer Networks course’s lab.

It is able to transfer files between two hosts. Set the `Server.py` on one host and the `client.py` on the other. Before you run the `Server.py`， you should open it, and modify the `SERVER_IP` into your inet-ip.

```python
SERVER_IP = '172.17.50.166'  # need to modify!!!
SERVER_PORT = 2222
```

Because this tool can just transfer files within the same directory,  you should move the file you wanna transfer to the same path as `Server.py` or `client.py`。

Run `Server.py` first by `python3 Server.py` and you will see `main server is ready` shows that it works well.

Run the `client.py`:

```shell
PS D:\network> python3 client.py
==================================
= Welcome to FDFTP application~~ =
==================================
For connecting the server, you should input
the server ip first, then:


Please input the file name which you wanna upload
or download, and the command with format:
----------------------------------
-u or upload for uploading
-d or download for downloading
-l or ls for listing the files in server
-q or quit for quiting
-h or help for this help itself
----------------------------------
If you find a bug or wanna more information,
please goto: https://github.com/Row11n/FDFTP


Server ip:
```

Shows that it works well.
