import concurrent.futures

import select
import socket
import threading
import time
import sys
import logging
import json
import pymssql  # 引入pymssql模块
from common import *
from UserManage import *
from concurrent.futures import ThreadPoolExecutor

PORT = 8888  # 定义服务器端口
lock = threading.Lock()  # 定义锁
sendBuffer = []  # 发送缓冲区，以字典形式存放要发送给用户的消息，
# 接收形式:{user_name:xx, target:xx, message:xx},
# 发送形式: {type: 表明该次发送的消息类型, user_name:xx,  message:xx}
user_sep = ":"  # 用于分割用户id和用户名的字符


class ChatServer(threading.Thread):
    global PORT, lock

    def __init__(self, port=PORT):
        threading.Thread.__init__(self)
        self.addr = ("", port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # 使用tcp协议通信
        self.online_users = OnlineUsers()  # 定义一个用来管理用户的数据结构
        self.pool = ThreadPoolExecutor(max_workers=10)  # 创建一个线程池
        self.input = []  # 定义select的输入
        self.output = []  # 定义写事件
        self.exception = []  # 定义异常事件列表

    # 处理新来的用户
    def handlerNewUser(self, conn, addr):
        # 1. 接收用户名 2. 添加到user中
        # 1. 接收用户名和密码
        data = conn.recv(1024).decode('utf-8')
        if len(data) == 0:
            print("用户退出!!")
            conn.close()
            self.input.remove(conn)
            return
        user_data = data.split(sep)
        print(f"接收到新用户提交的个人信息: {user_data}")
        user_name = user_data[1]
        password = user_data[2]
        if user_data[0] == '登录':
            self.online_users.AddUser(conn, user_name, addr, password)
            print("添加到在线用户列表成功")

    # 接收信息并放入sendBuffer中
    def Recv(self, sock):
        buffer = ""
        try:
            data = sock.recv(1024).decode('utf-8')  # 添加到缓冲区中
        except Exception as e:
            print(f"用户 {sock.getpeername()} 断开连接: {e}")
            self.online_users.DelUser(sock)
            self.input.remove(sock)
            return
        print(f"收到的消息: {data}")
        if len(data) == 0:
            # 客户端关闭, 需要从user中删除, 同时select中需要删除
            print("客户端关闭，我也即将关闭链接")
            sock.close()
            self.online_users.DelUser(sock)
            self.input.remove(sock)
            return
        buffer = buffer + data
        while True:
            pos = buffer.find(sep)
            if pos == -1:
                break
            length = int(buffer[:pos])
            if length > len(buffer):
                # 说明没有截取完整，那么就返回去继续接收
                break
            string = buffer[pos + 2:pos + 2 + length]
            buffer = buffer[pos + 2 + length:len(buffer)]  # 从buffer中删除已经提取的部分
            message = json.loads(string)
            try:
                lock.acquire()
                sendBuffer.append(message)
            finally:
                lock.release()
            print(f"message: {message}")
            continue

    def HandlerSendBuffer(self):
        time.sleep(1)
        global sendBuffer
        for data in sendBuffer:
            # 提取message的各种属性: {user_name:xx, target:xx, message:xx}
            print("检测到存在信息需要转发")
            user_name = data['user_name']
            target = data['target']
            message = data['message']
            print(f"当前消息来自: {user_name}")
            try:
                lock.acquire()
                sendBuffer.remove(data)  # 移除已经提取完毕的数据
            finally:
                lock.release()
            if len(target) == 0:  # 如果是群发，那么pattern指定为group
                # 群发
                user_dic = self.online_users.GetOnlineUser()
                send_data = {"type": "message", "user_name": user_name, "message": message}
                send_data = json.dumps(send_data)
                send_data = Encode(send_data)
                # 给每一个用户都发送消息, 包括自己
                for key, value in user_dic.items():
                    # 对信息做序列化后编码发送 {"user_name": 用户名, "message": 消息内容}
                    value.send(send_data.encode('utf-8'))
                    print("群发完毕")
            # 私聊 -- 根据target找到对应的用户发送
            else:
                user_dic = self.online_users.GetOnlineUser()
                send_data = {"type": "message", "user_name": user_name, "message": message}
                send_data = json.dumps(send_data)
                send_data = Encode(send_data)
                for user in target:
                    user_dic[user].send(send_data.encode('utf-8'))
                user_dic[user_name].send(send_data.encode('utf-8'))
                print("私发完毕")

    def consumer(self):
        self.pool.submit(self.HandlerSendBuffer)

    def producer(self, service_sock):
        future = self.pool.submit(self.Recv, service_sock)
        concurrent.futures.wait([future])

    # 1. 创建监听套接字
    # 2. 添加到select中
    def run(self):
        # 绑定内核，开始监听
        self.sock.bind(self.addr)
        self.sock.listen(5)
        # 把监听套接字添加到select中
        self.input.append(self.sock)
        # 开始服务
        while True:
            rlist, wlist, elist = select.select(self.input, self.output, self.exception)  # 阻塞式等待
            for r in rlist:
                if r is self.sock:
                    # 新连接到来, accept返回一个句柄以及元组0，元组中存储 ip和port
                    service_sock, addr = self.sock.accept()
                    # 将新的sock添加到select中
                    self.input.append(service_sock)
                    # 1. 接收一次用户发送过来的用户名信息，将用户添加到 users中进行管理
                    addr = (service_sock, addr)
                    future = self.pool.submit(self.handlerNewUser, *addr)
                    concurrent.futures.wait([future])
                else:
                    # 用户消息到来
                    # 这里必须要等待sendBuffer有数据
                    self.producer(r)
                    self.pool.submit(self.consumer)


if __name__ == "__main__":
    logging.basicConfig()
    chatServer = ChatServer()
    chatServer.start()
    while True:
        time.sleep(1)
        if not chatServer.is_alive():
            print("服务器挂了")
            chatServer.pool.shutdown()  # 关闭线程池
            sys.exit(1)
