import socket
import threading
from datetime import datetime
import struct
import os
import sys
import random
import time
import importlib
importlib.reload(sys)
import FDFTPsocket
import hashlib
from queue import PriorityQueue

normalPacket = struct.Struct('2I1024s')
ackPacket = struct.Struct('I')

BUF_SIZE = 1024+28
FILE_SIZE = 1024
SERVER_IP = '192.168.85.128'  # need to modify!!!
SERVER_PORT = 2222
ACK_SIZE = 4
RTT_INDEX = 2
DUPACK_LIMIT = 3
INTERVAL = 1

fastRecovery = 0
slowStart = 1
recoverySeq = 0
resendCount = 0
timerCount = 0
sampleRTT = 0
cWndLimit = 1
cWndCount = 0
lastSendSeq = 0
ssthresh = 20
cWndQueue = PriorityQueue()

def reinit_variables():
    global timerCount, INTERVAL, sampleRTT, slowStart
    global ssthresh, cWndLimit, recoverySeq, resendCount
    global cWndQueue
    fastRecovery = 0
    slowStart = 1
    recoverySeq = 0
    resendCount = 0
    timerCount = 0
    sampleRTT = 0
    cWndLimit = 1
    cWndCount = 0
    lastSendSeq = 0
    ssthresh = 100
    while not cWndQueue.empty():
        cWndQueue.get()


def get_file_md5(file_name):
    m = hashlib.md5()
    with open(file_name,'rb') as f:
        while True:
            data = f.read(4096)
            if not data:
                break
            m.update(data)
    return m.hexdigest()


def resend_by_ack(Task, s, client_addr):
    global ssthresh, lastSendSeq, cWndLimit, slowStart
    global timerCount, recoverySeq, cWndQueue

    if cWndQueue.empty():
        exit
    timerCount += 1
    ssthresh = max(cWndLimit / 2, 2)
    cWndLimit = ssthresh + DUPACK_LIMIT
    recoverySeq = lastSendSeq
    t = cWndQueue.get()
    Task.sendto(s, t[1], client_addr)
    # print("resend by ack "+str(normalPacket.unpack(t[1])[0]))
    cWndQueue.put(t)


def resend(Task, s, client_addr):
    global timerCount, INTERVAL, sampleRTT, slowStart
    global ssthresh, cWndLimit, recoverySeq, resendCount
    global cWndQueue

    if cWndQueue.empty():
        exit
    timerCount += 1
    resendCount += 1
    t = cWndQueue.get()
    Task.sendto(s, t[1], client_addr)
    # print("resend RTO"+str(normalPacket.unpack(t[1])[0]))
    cWndQueue.put(t)
    if INTERVAL < 4 * sampleRTT:
        INTERVAL += 0.1 * INTERVAL
    if slowStart == 0:
        ssthresh = cWndLimit / 2
        cWndLimit = 1
    slowStart = 1
    fastRecovery = 0


def upload_server(s, client_addr, file_name, fileSize, id):
    cacheQueue = PriorityQueue()
    s.settimeout(30)
    lostFlag = 0
    try:
        f = open(file_name, "wb")
        lastAcked = 0
        Task = FDFTPsocket.Task(file_name)
        while True:
            dataPack, client_addr = s.recvfrom(BUF_SIZE)
            unpacked_data = normalPacket.unpack(dataPack)
            seq = unpacked_data[0]
            endFlag = unpacked_data[1]
            data = unpacked_data[2]
            ack = seq + endFlag
            if seq == lastAcked:
                data = data[:endFlag]
                f.write(data)
                lastAcked = ack
                Task.sendto(s, ackPacket.pack(ack), client_addr)
                while True:
                    if cacheQueue.empty():
                        break
                    t = cacheQueue.get()
                    cacheData = normalPacket.unpack(t[1])
                    cacheSeq = cacheData[0]
                    if cacheSeq == lastAcked:
                        cacheEndFlag = cacheData[1]
                        cacheData = cacheData[2]
                        cacheData = cacheData[:cacheEndFlag]
                        f.write(cacheData)
                        lastAcked = cacheSeq + cacheEndFlag
                        Task.sendto(s, ackPacket.pack(lastAcked), client_addr)
                    elif cacheSeq < lastAcked:
                        continue
                    else:
                        cacheQueue.put(t)
                        break
            elif seq < lastAcked:
                Task.sendto(s, ackPacket.pack(lastAcked), client_addr)
            else:
                cacheQueue.put((seq, dataPack))
                Task.sendto(s, ackPacket.pack(lastAcked), client_addr)

            # print("lastAcked " + str(lastAcked))
            if lastAcked == fileSize:
                Task.finish()
                break

    except Exception as e:
        print(e)
        lostFlag = 1
        pass

    f.close()
    
    if lostFlag == 0:
        # File verification
        while True:
            s.settimeout(10)
            try:
                data, client_addr = s.recvfrom(BUF_SIZE)
                # print("recvd the md5sum")
                data = normalPacket.unpack(data)
                seq = data[0]
                if seq < fileSize:
                    continue
                else:
                    data = str(data[2].decode())
                data = data.split('\x00')[0]
                if data == str(get_file_md5(file_name)):
                    s.sendto(b'Verification pass!', client_addr)
                else:
                    s.sendto(b'Verification error, need to reupload', client_addr)
            except Exception as e:
                print(e)
                break
    
    print('user' + str(id) + ' is closed, info:' + str(client_addr))
    s.close()


def download_server(s, client_addr, file_name, id):
    global fastRecovery, slowStart, recoverySeq, resendCount
    global timerCount, sampleRTT, cWndLimit, cWndCount
    global cWndQueue, lastSendSeq, ssthresh
 
    # connection
    init_data, client_addr = s.recvfrom(BUF_SIZE)
    sampleRTT = float(init_data.decode())
    print("sampleRTT = " + str(sampleRTT))
    print(file_name)
    fileSize = os.path.getsize(file_name)
    Task = FDFTPsocket.Task(file_name)
    taskStartTime = time.time()
    f = open(file_name,"rb")
    restSize = fileSize
    print("file_size is: " + str(fileSize))
    
    endFlag = int(FILE_SIZE)
    seq = 0
    lastAcked = 0
    INTERVAL = RTT_INDEX * sampleRTT
    repeat = 0

    while True:
        while cWndQueue.qsize() < cWndLimit:
            data = f.read(FILE_SIZE)
            if data == b'':
                break
            if restSize < FILE_SIZE:
                endFlag = int(restSize)
            else:
                restSize = restSize - FILE_SIZE
                endFlag = FILE_SIZE
            sendPacket = normalPacket.pack(seq, endFlag, data)
            cWndQueue.put((seq, sendPacket))
            Task.sendto(s, sendPacket, client_addr)
            lastSendSeq = seq
            # print("send "+ str(seq) + " put " + str(seq))
            seq += FILE_SIZE

        while True:
            try:
                s.settimeout(INTERVAL)
                timer = threading.Timer(INTERVAL, resend, args=(Task, s, client_addr))
                timer.setDaemon(True)
                timer.start()
                data, client_addr = s.recvfrom(ACK_SIZE)
                INTERVAL = max(RTT_INDEX * sampleRTT, INTERVAL - 0.01 * INTERVAL)
                timer.cancel()
                if slowStart or fastRecovery:
                    cWndLimit += 1
                    if cWndLimit >= ssthresh:
                        slowStart = 0
                    if cWndLimit >= 2 * ssthresh:
                        fastRecovery = 0
                else:
                    cWndCount += 1
                    if cWndCount >= cWndLimit:
                        cWndCount = 0
                        cWndLimit += 1
                break
            except Exception as e:
                continue
        s.settimeout(None)

        ack = ackPacket.unpack(data)[0]
        # print("ack: "+ str(ack))
        if ack == lastAcked:
            repeat += 1
            if repeat >= DUPACK_LIMIT and fastRecovery == 0 and slowStart == 0:
                resend_by_ack(Task, s, client_addr)
                repeat = 0
                fastRecovery = 1
        elif ack > lastAcked:
            repeat = 0
            while True:
                if cWndQueue.empty():
                    break
                t = cWndQueue.get()
                # print("get " + str(t[0]))
                if t[0] >= ack:
                    cWndQueue.put(t)
                    # print("put " + str(t[0]))
                    break
            lastAcked = ack
            if slowStart == 1:
                fastRecovery = 0
            elif fastRecovery == 1 and slowStart == 0 and lastAcked > recoverySeq:
                fastRecovery = 0
                cWndLimit = ssthresh

        if ack == fileSize:
            break

    Task.finish() 
    totalTime = time.time() - taskStartTime
    f.close()
    i = 0
    while True:
        s.settimeout(3)
        try:
            data = str(get_file_md5(file_name)) + ';' + str(timerCount)
            sendPacket = normalPacket.pack(seq, 0, data.encode())
            s.sendto(sendPacket, client_addr)
            data, client_addr = s.recvfrom(BUF_SIZE)
            data = data.decode()
            print(data)
            break
        except Exception as e:
            print('the sending pack of md5sum is lost. Auto-retry...')
            i += 1
            if i == 5:
                break
            continue
    s.settimeout(None)
    reinit_variables()
    print('user' + str(id) + ' is closed, info:' + str(client_addr))
    s.close()

def file_list_server(s, client_addr):
    data, client_addr = s.recvfrom(BUF_SIZE)
    listing = os.listdir(os.getcwd())
    list_string = ''
    for i in listing:
        list_string += ';' + i
    i = 0
    while True:
        s.settimeout(3)
        try:
            sendPacket = normalPacket.pack(0, 0, list_string.encode())
            s.sendto(sendPacket, client_addr)
            data, client_addr = s.recvfrom(BUF_SIZE)
            break
        except Exception as e:
            print('the sending pack of file list is lost. Auto-retry...')
            i += 1
            if i == 5:
                break
            continue
    s.close()


class Server():
    def __init__(self):
        self.cid = 0
        self.address = (SERVER_IP, SERVER_PORT)
        self.server_socket = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        self.server_socket.bind(self.address)
        # self.server_socket.listen(64)

    def port_distribute(self):
        while True:
            data, client_addr = self.server_socket.recvfrom(BUF_SIZE)
            data = data.decode().split(';')
            file_name = data[1]
            command = data[3]
            if command == 'upload':
                fileSize = int(data[2])
            elif command == 'download':
                fileSize = os.path.getsize(file_name)
            elif command == 'list':
                fileSize = 0
            if data[0] == "hello, big root":
                newSocket = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
                newSocket.bind((SERVER_IP, 0))
                addr = newSocket.getsockname()
                init_string = addr[0] + ";" + str(addr[1]) + ";" + str(fileSize)
                self.server_socket.sendto(init_string.encode(), client_addr)
                thread = threading.Thread(target=self.trans, args=(newSocket, client_addr, file_name, fileSize, command))
                thread.setDaemon(True)
                thread.start()

    def trans(self, s, client_addr, file_name, fileSize, command):
        self.cid += 1
        id = self.cid
        print('user' + str(id) + ' is ready, info:' + str(client_addr))
        if command == 'upload':
            upload_server(s, client_addr, file_name, fileSize, id)
        elif command == 'download':
            download_server(s, client_addr, file_name, id)
        elif command == 'list':
            file_list_server(s, client_addr)
                            
    def start(self):
        print('main server is ready')
        thread = threading.Thread(target=self.port_distribute, args=())
        thread.setDaemon(True)
        thread.start()


if __name__=='__main__' :
    ftp_server = Server()
    ftp_server.start()
    while True:
        q = input()
        if q == 'quit':
            break
    
    