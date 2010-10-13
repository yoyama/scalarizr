import time
import os
import signal
import re
import select
from threading import Thread, Event
import paramiko

class SshPool:
	def __new__(self):
		pass
	
	def add(self, name, ssh):
		pass
	
	def destroy(self, name):
		pass
	
	def destroy_pool(self):
		pass
	
	def idle_thread(self):
		'''
		NOOP ssh sessions
		'''
		pass

regexps = ['root@.*#',
		   '.+:.*#',
		   'local2:.*#',
		   '\-bash\-.*#']

root_re = '|'.join(regexps)

class SshManager:
	
	transport = None
	connected = False
	channels  = []
	
	def __init__(self, host, key, timeout = 60, key_pass = None):
		self.host = host
		key_file = os.path.expanduser(key)
		if not os.path.exists(key_file):
			raise Exception("Key file '%s' doesn't exist", key_file)
		self.key = paramiko.RSAKey.from_private_key_file(key_file, password = key_pass if key_pass else None)

		self.timeout = timeout
		self.user = 'root'
		self.ssh = paramiko.SSHClient()
		self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
		
	def connect(self):
		start_time = time.time()
		while time.time() - start_time < self.timeout:
			try:
				self.ssh.connect(self.host, pkey = self.key, username=self.user)
				break
			except:
				continue
		else:
			raise Exception("Cannot connect to server %s" % self.host)
		
		transport = self.ssh.get_transport()
		channel = transport.open_session()
		channel.get_pty()
		channel.invoke_shell()
		time.sleep(1)
		
		if channel.closed:
			raise Exception ("Can't open new session")
		
		out = ''
		while channel.recv_ready():
			out += channel.recv(1)
			
		if 	'Please login as the ubuntu user rather than root user' in out:
			self.user = 'ubuntu'
			self.connect()
		else:
			self.connected = True
		channel.close()
		
		
	def get_root_ssh_channel(self):
		
		if not self.connected:
			self.connect()
			
		if not self.transport:
			self.transport = self.ssh.get_transport()
			self.transport.set_keepalive(60)
			
		channel = self.ssh.invoke_shell()
		channel.resize_pty(500, 500)
		self.channels.append(channel)
		if self.user == 'ubuntu':
			channel.send('sudo -i\n')
			
		out = clean_output(channel, 5)	
		print "Returned channel: %s" % out		
		return channel
	
	def get_sftp_client(self):
		return self.ssh.open_sftp()

class LogReader:
	
	_exception = None
	_error     = ''
	
	def __init__(self):
		self.err_re = re.compile('^\d+-\d+-\d+\s+\d+:\d+:\d+,\d+\s+-\s+ERROR\s+-\s+.+$', re.M)
		self.traceback_re = re.compile('Traceback.+')

	def expect(self, regexp, timeframe, channel):
		self.out = ''
		self._error = ''
		self.ret = None
		break_tail = Event()

		t = Thread(target =self.reader_thread, args=(channel, regexp, break_tail))
		t.start()
		
		start_time = time.time()
		
		while time.time() - start_time < timeframe:
			time.sleep(0.1)
			if break_tail.is_set():
				if self._error:
					raise Exception('Error detected: %s' % self._error)
				if self.ret:
					return self.ret
				else:
					raise Exception('Something bad happened')
		else:
			break_tail.set()
			raise Exception('Timeout after %s.' % timeframe)				

	def reader_thread(self, channel, regexp, break_tail):
		search_re = re.compile(regexp) if type(regexp) == str else regexp
		while not break_tail.is_set():
			while channel.recv_ready():
				self.out += channel.recv(1)
				if self.out[-1] == '\n':
					break
			
			error = re.search(self.err_re, self.out)
			if error:
				# Detect if traceback presents
				traceback = ''
				while channel.recv_ready():
					traceback += channel.recv(1)
					if traceback[-1] == '\n':
						break
				
				if re.search(self.traceback_re, traceback):
					newline = ''
					while channel.recv_ready():
						newline += channel.recv(1)
						if newline[-1] == '\n':
							newline
							traceback += newline
							if not newline.startswith(' '):
								break
							newline = ''

					self._error = error.group(0) + traceback
				else:
					self._error = error.group(0)
				break_tail.set()
				break
				
			if re.search(search_re, self.out):
				self.ret = re.search(search_re, self.out)
				break_tail.set()
				break

def tail_log_channel(channel):
	if channel.closed:
		raise Exception('Channel is closed')
	
	while channel.recv_ready():
		channel.recv(1)
		
	cmd = 'tail -f -n 0 /var/log/scalarizr.log\n'
	channel.send(cmd)
	channel.recv(len(cmd))

	
def expect(channel, regexp, timeframe):
	reader = LogReader()
	return reader.expect(regexp, timeframe, channel)

def exec_command(channel, cmd, timeout = 60):
	
	while channel.recv_ready():
		channel.recv(1)
	bytes_amount = channel.send(cmd)
	time.sleep(0.3)
	channel.recv(bytes_amount)
	channel.send('\n')
	out = clean_output(channel, timeout)
	lines = out.splitlines()
	if len(lines) > 2:
		return '\n'.join(lines[1:-1]).strip()
	else:
		return ''
	
def clean_output(channel, timeout = 60):
	out = ''
	
	#not the best solution
	#if not channel.recv_ready():
	#	return out
	
	last_recv_time = time.time()
	while True:
		if channel.recv_ready():
			last_recv_time = time.time()
			out += channel.recv(1024)
			if re.search(root_re, out):
				break
		else:
			if time.time() - last_recv_time > timeout:
				raise Exception('Timeout (%s sec) while waiting for root prompt. Out:\n%s' % (timeout, out))	
	return out