import socket
import struct
import os
import stat
import re
import sys
import time
import random
import threading
from queue import PriorityQueue
import FDFTPsocket
import hashlib
import importlib
importlib.reload(sys)

normalPacket = struct.Struct('2I1024s')
ackPacket = struct.Struct('I')

SERVER_PORT = 2222
DEFUALT_SERVER_IP = '8.218.117.184'
FILE_SIZE = 1024
BUF_SIZE = 1024+28
ACK_SIZE = 4            # The size of ack packet
RTT_INDEX = 2           # to compute the estRTT
DUPACK_LIMIT = 3        # The limit of dupACK
INTERVAL = 1            # The default of RTO interval

fastRecovery = 0        # The status of FastRecovery, default 0
slowStart = 1           # The status of SlowStart, default 1
recoverySeq = 0         # to mark the sequence to return normal status
resendCount = 0         # for debug
timerCount = 0          # for debug
sampleRTT = 0
cWndLimit = 1
cWndCount = 0           # to record the count to add cwndLimit when CA
lastSendSeq = 0
ssthresh = 50
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

#resend a pack for dupACK
def resend_by_ack(Task, s, server_addr):
    global ssthresh, lastSendSeq, cWndLimit, slowStart
    global timerCount, recoverySeq, cWndQueue

    if cWndQueue.empty():
        exit()
    timerCount += 1
    ssthresh = max(cWndLimit / 2, 2)
    cWndLimit = ssthresh + DUPACK_LIMIT
    recoverySeq = lastSendSeq
    t = cWndQueue.get()
    Task.sendto(s, t[1], server_addr)
    # print("resend by ack "+str(normalPacket.unpack(t[1])[0]))
    cWndQueue.put(t)   

#resend a pack for RTO
def resend(Task, s, server_addr):
    global timerCount, INTERVAL, sampleRTT, slowStart
    global ssthresh, cWndLimit, recoverySeq, resendCount
    global cWndQueue, fastRecovery

    if cWndQueue.empty():
        exit()
    timerCount += 1
    resendCount += 1
    t = cWndQueue.get()
    Task.sendto(s, t[1], server_addr)
    # print("resend RTO "+str(normalPacket.unpack(t[1])[0]))
    cWndQueue.put(t)
    if INTERVAL < 4 * sampleRTT:
        INTERVAL += 0.1 * INTERVAL
    if slowStart == 0:
        ssthresh = cWndLimit / 2
        cWndLimit = 1
    slowStart = 1
    fastRecovery = 0


def upload(server_ip, file_name):
    global fastRecovery, slowStart, recoverySeq, resendCount
    global timerCount, sampleRTT, cWndLimit, cWndCount
    global cWndQueue, lastSendSeq, ssthresh

    # connect to the server
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    init_server_addr = (server_ip, SERVER_PORT)
    fileSize = os.path.getsize(file_name)
    if fileSize == 0:
        print("Error: an empty file!")
        s.close()
        exit()
    totalPack = fileSize / FILE_SIZE + 1
    init_string = 'hello, big root;' + file_name + ';' + str(fileSize) + ';' + 'upload'
    while True:
        s.settimeout(5)
        try:
            s.sendto(init_string.encode(), init_server_addr)
            startTime = time.time()
            init_data, init_server_addr = s.recvfrom(BUF_SIZE)
            sampleRTT = max(time.time() - startTime, 0.1)
            addr = init_data.decode()
            port = int(addr.split(";")[1])
            server_addr = (server_ip, port)    
            break
        except Exception as e:
            print('Initial Info is lost. Auto-retry...')
            continue
    s.settimeout(None)

    #begin to upload
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
        # send packs:
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
            Task.sendto(s, sendPacket, server_addr)
            lastSendSeq = seq
            # print("send "+ str(seq) + " put " + str(seq))
            seq += FILE_SIZE

        # receive ack:
        while True:
            try:
                s.settimeout(INTERVAL)
                timer = threading.Timer(INTERVAL, resend, args=(Task, s, server_addr))
                timer.setDaemon(True)
                timer.start()
                data, server_addr = s.recvfrom(ACK_SIZE)
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

        # handle ack:
        ack = ackPacket.unpack(data)[0]
        # print("ack: "+str(ack))
        if ack == lastAcked:
            repeat += 1
            if repeat >= DUPACK_LIMIT and fastRecovery == 0 and slowStart == 0:
                resend_by_ack(Task, s, server_addr)
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
            deltaTime = max(time.time() - startTime, 0.0001)
            speed = round(lastAcked / deltaTime / 1e6, 2)
            progress = round(lastAcked / fileSize * 100, 2)
            print("\r speed: " + str(speed) + "MB/s" + " progress: " + str(progress) + "%", end="")
            if slowStart == 1:
                fastRecovery = 0
            elif fastRecovery == 1 and slowStart == 0 and lastAcked > recoverySeq:
                fastRecovery = 0
                cWndLimit = ssthresh

        # the end flag:
        if ack == fileSize:
            break
    print('\n')
    Task.finish() 
    totalTime = time.time() - taskStartTime
    f.close()

    # File Verification
    while True:
        s.settimeout(3)
        try:
            sendPacket = normalPacket.pack(seq, 0, str(get_file_md5(file_name)).encode())
            s.sendto(sendPacket, server_addr)
            data, server_addr = s.recvfrom(BUF_SIZE)
            data = data.decode()
            print(data)
            break
        except Exception as e:
            print('the sending pack of md5sum is lost. Auto-retry...')
            continue
    s.settimeout(None)

    print("resend_count: " + str(resendCount))
    print("timer_count: " + str(timerCount))
    print("Packet Loss Rate: " + str((1 - totalPack / (totalPack + timerCount)) * 100) + "%")
    print("speed: " + str(round(fileSize / totalTime / 1e6, 2)) + "MB/s")
    reinit_variables()
    s.close()


def download(server_ip, file_name):
    # connect to the server
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    init_server_addr = (server_ip, SERVER_PORT)
    fileSize = 0
    totalPack = fileSize / FILE_SIZE + 1
    init_string = 'hello, big root;' + file_name + ';' + str(fileSize) + ';' + 'download'
    while True:
        s.settimeout(5)
        try:
            s.sendto(init_string.encode(), init_server_addr)
            startTime = time.time()
            init_data, init_server_addr = s.recvfrom(BUF_SIZE)
            sampleRTT = max(time.time() - startTime, 0.06)
            addr = init_data.decode()
            ip = server_ip
            port = int(addr.split(";")[1])
            fileSize = int(addr.split(";")[2])
            totalPack = fileSize / FILE_SIZE
            server_addr = (ip, port)    
            # for NAT
            s.sendto(str(sampleRTT).encode(), server_addr)
            break
        except Exception as e:
            print('Initial Info is lost. Auto-retry...')
            continue
    s.settimeout(None)

    cacheQueue = PriorityQueue()
    while True:
        s.settimeout(5)
        try:
            f = open(file_name, "wb")
            lastAcked = 0
            Task = FDFTPsocket.Task(file_name)
            startTime = time.time()
            while True:
                dataPack, server_addr = s.recvfrom(BUF_SIZE)
                s.settimeout(None)
                unpacked_data = normalPacket.unpack(dataPack)
                seq = unpacked_data[0]
                endFlag = unpacked_data[1]
                data = unpacked_data[2]
                ack = seq + endFlag
                if seq == lastAcked:
                    data = data[:endFlag]
                    f.write(data)
                    lastAcked = ack
                    Task.sendto(s, ackPacket.pack(ack), server_addr)
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
                            Task.sendto(s, ackPacket.pack(lastAcked), server_addr)
                        elif cacheSeq < lastAcked:
                            continue
                        else:
                            cacheQueue.put(t)
                            break

                elif seq < lastAcked:
                    Task.sendto(s, ackPacket.pack(lastAcked), server_addr)
                else:
                    cacheQueue.put((seq, dataPack))
                    Task.sendto(s, ackPacket.pack(lastAcked), server_addr)

                deltaTime = max(time.time() - startTime, 0.0001)
                speed = round(lastAcked / deltaTime / 1e6, 2)
                progress = round(lastAcked / fileSize * 100, 2)
                print("\r speed: " + str(speed) + "MB/s" + " progress: " + str(progress) + "%", end="")
                # print("lastAcked " + str(lastAcked))
                if lastAcked == fileSize:
                    print('\n')
                    totalTime = time.time() - startTime
                    Task.finish()
                    break
            break
        except Exception as e:
            s.sendto(str(sampleRTT).encode(), server_addr)
            continue
    f.close()

    while True:
        s.settimeout(3)
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
            md5 = data.split(';')[0]
            timerCount = int(data.split(';')[1])
            if md5 == str(get_file_md5(file_name)):
                s.sendto(b'Verification pass!', client_addr)
                print('Verification pass!')
                break
            else:
                s.sendto(b'Verification error, need to redownload', client_addr)
                print('Verification error, need to redownload')
                break
        except Exception as e:
            print(e)
            break
    print("Packet Loss Rate: " + str((1 - totalPack / (totalPack + timerCount)) * 100) + "%")
    print("speed: " + str(round(fileSize / totalTime / 1e6, 2)) + "MB/s")
    s.close()


def file_list():
    # connect to the server
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    init_server_addr = (server_ip, SERVER_PORT)
    init_string = 'hello, big root;' + file_name + ';' + '0' + ';' + 'list'
    while True:
        s.settimeout(5)
        try:
            s.sendto(init_string.encode(), init_server_addr)
            init_data, init_server_addr = s.recvfrom(BUF_SIZE)
            addr = init_data.decode()
            port = int(addr.split(";")[1])
            server_addr = (server_ip, port)    
            s.sendto('hi'.encode(), server_addr)
            break
        except Exception as e:
            print('Initial Info is lost. Auto-retry...')
            continue

    orgin_list_string, server_addr = s.recvfrom(BUF_SIZE)
    orgin_list_string = normalPacket.unpack(orgin_list_string)
    list_string = str(orgin_list_string[2].decode()).split('\x00')[0]
    list_string = [i for i in list_string.split(';') if i != '']
    s.sendto('good'.encode(), server_addr)
    print(list_string)
    s.close()


def help():
    print('\n')
    print("Please input the file name which you wanna upload")
    print("or download, and the command with format:")
    print('----------------------------------')
    print("-u or upload for uploading")
    print("-d or download for downloading")
    print("-l or ls for listing the files in server")
    print("-q or quit for quiting")
    print("-h or help for this help itself")
    print('----------------------------------')
    print("If you find a bug or wanna more information,")
    print("please goto: https://github.com/Row11n/FDFTP")
    print('\n')



if __name__ == "__main__":
    print('==================================')
    print('= Welcome to FDFTP application~~ =')
    print('==================================')
    print("For connecting the server, you should input")
    print("the server ip first, then:")
    help()
    server_ip = input('Server ip: ').strip()
    if server_ip == '-q' or server_ip == 'quit':
        exit()
    while True:
        while True:
            file_name = input('File name: ').strip()
            if file_name == '-q' or file_name == 'quit':
                exit()
            elif file_name == '-h' or file_name == 'help':
                help()
            elif file_name == '-l' or file_name == 'ls':
                file_list()
            else:
                break
        command = input('Command: ').strip()
        if command == '-u' or command == 'upload':
            upload(server_ip, file_name)
        elif command == '-d' or command == 'download':
            download(server_ip, file_name)
        elif command == '-h' or command == 'help':
            help()
        elif command == '-q' or command == 'quit':
            break
        else:
            print('command error, plese try again.')