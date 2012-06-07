'''
Created on Nov 8, 2011

@author: dmitry
'''
import os
import logging
import re
import subprocess
import signal
import pymysql
import random
import pwd
import threading
import time

from pymysql import cursors

from scalarizr.config import BuiltinBehaviours
from scalarizr.services import  BaseService, ServiceError, BaseConfig, lazy
from scalarizr.util import system2, disttool, firstmatched, initdv2, wait_until, PopenError, software, filetool
from scalarizr.util.initdv2 import wait_sock, InitdError
from scalarizr.util.filetool import rchown


LOG = logging.getLogger(__name__)

MYSQL_DEFAULT_PORT=3306
MYSQL_PATH  = '/usr/bin/mysql' # old mysql_path
MYCNF_PATH 	= '/etc/mysql/my.cnf' if disttool.is_ubuntu() else '/etc/my.cnf' 
MYSQLD_PATH = '/usr/sbin/mysqld'  if disttool.is_ubuntu() else '/usr/libexec/mysqld' #old mysqld_path
MYSQLDUMP_PATH = '/usr/bin/mysqldump'
DEFAULT_DATADIR	= "/var/lib/mysql"

STORAGE_DATA_DIR = "mysql-data"
STORAGE_BINLOG = "mysql-misc/binlog"

BEHAVIOUR = SERVICE_NAME = BuiltinBehaviours.MYSQL


class MySQL(BaseService):
	
	service = None
	my_cnf = None
	_instance = None
	
		
	def __new__(cls, *args, **kwargs):
		if not cls._instance:
			cls._instance = super(MySQL, cls).__new__(
								cls, *args, **kwargs)
		return cls._instance
	
		
	def __init__(self):
		self._objects = {}
		self.service = initdv2.lookup(SERVICE_NAME)

	
	def _init_replication(self, master=True):
		LOG.info('Initializing replication')
		server_id = 1 if master else int(random.random() * 100000)+1
		self.my_cnf.server_id = server_id
		self.my_cnf.bind_address = None
		self.my_cnf.skip_networking = None

	
	def init_master(self):
		pass
	
	def init_slave(self):
		pass
	
	def _init_service(self):
		pass
	
	def change_master_to(self):
		# client.change_master_to
		# check_replication_health and wait 
		pass

	def check_replication_health(self):
		# set slave status
		# on fail get status from error.log
		pass
			
	@property
	def version(self):
		#5.1/5.5
		#percona/mysql
		pass	

	
	def move_mysqldir_to(self, storage_path):
		LOG.info('Moving mysql dir to %s' % storage_path)
		for directive, dirname in (
				('mysqld/log_bin', os.path.join(storage_path,STORAGE_BINLOG)), 
				('mysqld/datadir', os.path.join(storage_path,STORAGE_DATA_DIR) + '/')
				): 
			
			dest = os.path.dirname(dirname)
			if os.path.isdir(dest):
				LOG.info('No need to move %s to %s: already in place.' % (directive, dest))
			else:
				os.makedirs(dest)
				
				raw_value = self.my_cnf.get(directive)
				LOG.debug('directive %s:%s' % (directive, raw_value))
				if raw_value:
					src_dir = os.path.dirname(raw_value + "/") + "/"
					LOG.debug('source path: %s' % src_dir)
					if os.path.isdir(src_dir) and src_dir != dest:
						
						set_se_path = software.whereis('setsebool')
						if set_se_path:
							LOG.debug('Make SELinux rule for rsync')
							system2((set_se_path[0], 'rsync_disable_trans', 'on'), raise_exc=False)
							
						LOG.info('Copying mysql directory \'%s\' to \'%s\'', src_dir, dest)
						rsync = filetool.Rsync().archive()
						rsync.source(src_dir).dest(dest).exclude(['ib_logfile*'])
						system2(str(rsync), shell=True)
			self.my_cnf.set(directive, dirname)
	
			rchown("mysql", dest)
			# Adding rules to apparmor config 
			if disttool.is_debian_based():
				_add_apparmor_rules(dest)

	
	def flush_logs(self, data_dir):
		LOG.info('Flushing logs')
		if not os.path.exists(data_dir):
			return
		
		info_files = ['relay-log.info', 'master.info']
		files = os.listdir(data_dir)
		
		for file in files:
			if file in info_files or file.find('relay-bin') != -1:
				os.remove(os.path.join(data_dir, file))
				
		
	def _get_my_cnf(self):
		return self._get('my_cnf', MySQLConf.find)
	
	
	def _set_my_cnf(self, obj):
		self._set('my_cnf', obj)	
				
		
	my_cnf = property(_get_my_cnf, _set_my_cnf)
	
	
class MySQLClient(object):
	_pool = dict()
	
	def __init__(self, user=None, passwd=None, db=None):
		self.db = db
		self.user = user
		self.passwd = passwd or ''
			
	
	def test_connection(self):
		LOG.debug('Checking MySQL service status')
		try:
			self.fetchone('SELECT 1')
		except pymysql.err.OperationalError, e:
			if 'Access denied for user' in str(e):
				return True
			elif "Can't connect to MySQL server on":
				return False
		except BaseException, e:
			LOG.debug('test_connection returned error: %s' % e)
			raise
		return True

	
	def list_databases(self):
		out = self.fetchall('SHOW DATABASES')
		LOG.debug('databases: %s' % str(out))
		databases = [db[0] for db in out if db]
		if 'information_schema' in databases:
			databases.remove('information_schema')
		if 'performance_schema' in databases:
			databases.remove('performance_schema')
		return databases
		
	
	def start_slave(self):
		return self.fetchone('START SLAVE')
	
			
	def stop_slave(self):
		return self.fetchone("STOP SLAVE")
			
			
	def reset_slave(self):
		return self.fetchone("RESET SLAVE")


	def stop_slave_io_thread(self):
		return self.fetchone("STOP SLAVE IO_THREAD")
	
	
	def lock_tables(self):
		return self.fetchone('FLUSH TABLES WITH READ LOCK')
	
		
	def unlock_tables(self):
		return self.fetchone('UNLOCK TABLES')
	
	
	def create_user(self, login, host, password, privileges=None):
		priv_count = self._priv_count()		
		if not privileges:
			'''
			XXX: temporary solution for mysql55
			'''
			cmd = "INSERT INTO mysql.user VALUES('%s','%s',PASSWORD('%s')" % (host, login, password) + ",'Y'"*priv_count
			if len(self.fetchdict("select * from mysql.user LIMIT 1;")) == 42:
				cmd += ",'','','','',0,0,0,0,'',''"
			else:
				cmd += ",''"*4 +',0'*4
			cmd += ");" 
		else:
			cmd = "INSERT INTO mysql.user (Host, User, Password, %s) VALUES ('%s','%s',PASSWORD('%s'), %s);" \
					% (', '.join(privileges), host,login,password, ', '.join(["'Y'"]*len(privileges)))
		self.fetchone(cmd)
		self.flush_privileges()
	
	
	def remove_user(self, login, host):
		return self.fetchone("DELETE FROM mysql.user WHERE User='%s' and Host='%s'" % (login, host))
	
	
	def user_exists(self, login, host):
		ret = self.fetchone("select User,Host from mysql.user where User='%s' and Host='%s'" % (login, host))
		
		#return True if ret and ret['Host']==host and ret['User']==login else False
		result = True if ret and len(ret)==2 and ret[0]==login and ret[1]==host else False 
		LOG.debug('user_exists query returned value: %s for user %s on host %s. User exists: %s' % (str(ret), login, host, str(result)))
		return result
		
	def flush_privileges(self):
		return self.fetchone("FLUSH PRIVILEGES")
	
			
	def change_master_to(self, host, user, password, log_file, log_pos):
		return self.fetchone('CHANGE MASTER TO MASTER_HOST="%(host)s", \
						MASTER_USER="%(user)s", \
						MASTER_PASSWORD="%(password)s", \
						MASTER_LOG_FILE="%(log_file)s", \
						MASTER_LOG_POS=%(log_pos)s, \
						MASTER_CONNECT_RETRY=15;' % vars())
	
	
	def slave_status(self):
		variables = dict(
				Slave_IO_Running=None, 
				Slave_SQL_Running=None,
				Last_Error = None,
				Last_Errno = None,
				Exec_Master_Log_Pos = None,
				Relay_Master_Log_File = None,
				Master_Log_File = None,
				Read_Master_Log_Pos = None
				)
					
		out = self.fetchdict("SHOW SLAVE STATUS")
		LOG.debug('slave status: %s' % str(out))
		if out:
			for name, value in out.items():
				if name in variables.keys():
					variables[name] = value
		else:
			raise ServiceError('SHOW SLAVE STATUS returned empty set. Slave is not started?')
		return variables
	
	
	def master_status(self):
		out = self.fetchdict('SHOW MASTER STATUS')
		log_file, log_pos = None, None
		if out:
			log_file, log_pos = out['File'], out['Position']
		return (log_file, log_pos)
	
	
	def reset_master(self):
		return self.fetchone("RESET MASTER")
	
	
	def show_global_variables(self):
		d = {} 
		raw = self.fetchdict('SHOW GLOBAL VARIABLES', fetch_one=False)
		LOG.debug('global variables: %s' % str(raw))
		for row in raw:
			name = row['Variable_name']
			val = row['Value']
			d[name] = val
		return d
		
	def check_password(self, user, password):
		hash_pairs = self.fetchall("SELECT PASSWORD('%s') AS hash, Password AS valid_hash FROM mysql.user WHERE mysql.user.User = '%s';" % 
				(password, user))
		
		for pair in hash_pairs:
			if pair[0] != pair[1]:
				return False
			
		return True
		
		
	def version(self):
		return self.fetchone('SELECT VERSION()')
	
		
	def get_connection(self, force=False):
		creds = (self.user, self.passwd, self.db)
		if force or not creds in self._pool:
			self._pool[creds] = pymysql.connect(host="127.0.0.1", user=self.user, passwd=self.passwd, db=self.db)
		return self._pool[creds]
	
	
	def _priv_count(self):
		res = self.fetchdict("select * from mysql.user LIMIT 1;")
		return len([r for r in res.keys() if r.endswith('priv')])
	
		
	def _fetch(self, query, cursor_type = None, fetch_one=False):
		conn = self.get_connection()
		cur = conn.cursor(cursor_type)
		LOG.debug(query)
		try:
			cur.execute(query)
		except pymysql.err.OperationalError, e:
			#catching mysqld restarts (e.g. sgt)
			if e.args[0] == 2013:
				conn = self.get_connection(force=True)
				cur = conn.cursor(cursor_type)
				cur.execute(query)
		res = cur.fetchone() if fetch_one else cur.fetchall()
		return res


	def fetchdict(self, query, fetch_one=True):
		return self._fetch(query, cursors.DictCursor, fetch_one)
	
	
	def fetchall(self, query):
		return self._fetch(query)
	
	
	def fetchone(self, query):
		return self._fetch(query, fetch_one=True)


class MySQLUser(object):
	
	login = None
	password = None
	host = None
	privileges = None
	
	def __init__(self, client, login, password=None, host=None, privileges=None):
		self.cli = client
		self.login = login
		self.password = password
		self.host = host
		self.privileges = privileges
	
	
	def create(self):
		if self.exists():
			raise ServiceError('Unable to create user %s@%s: already exists.')
		LOG.debug('Creating user %s on host %s with password %s and privileges %s' % (self.login, self.host, self.password, self.privileges))
		self.cli.create_user(self.login, self.host, self.password, self.privileges) 
		return self
	
	
	def check_password(self):
		if not self.exists():
			return False
		return self.cli.check_password(self.login, self.password)
	
	
	def exists(self):
		result = False
		try:
			result = self.cli.user_exists(self.login, self.host)
		except pymysql.err.OperationalError, e:
			raise ServiceError(str(e))
		return result
	
	
	def remove(self):
		return self.cli.remove_user(self.login, self.host)
		

	
class DataDir(object):
	
	def __init__(self):
		pass
	
	def move_to(self, dst):
		pass
	

class MySQLConf(BaseConfig):
	
	config_type = 'mysql'
	config_name = 'my.cnf'
	comment_empty = True
	
	
	@classmethod
	def find(cls):
		return cls(MYCNF_PATH)
		
	
	def _get_datadir(self):
		return self.get('mysqld/datadir')
	
	
	def _set_datadir(self, path):
		self.set('mysqld/datadir', path)	


	def _get_log_bin(self):
		return self.get('mysqld/log-bin')
	
	
	def set_log_bin(self, path):
		self.set('mysqld/log-bin', path)	


	def _get_server_id(self):
		return self.get('mysqld/server-id')
	
	
	def set_server_id(self, id):
		self.set('mysqld/server-id', id)	


	def _get_bind_address(self):
		return self.get('mysqld/bind-address')
	
	
	def set_bind_address(self, addr):
		self.set('mysqld/bind-address', addr)	


	def _get_skip_networking(self):
		return self.get('mysqld/skip-networking')
	
	
	def set_skip_networking(self, val):
		self.set('mysqld/skip-networking', val)	


	log_bin = property(_get_log_bin, set_log_bin)
	server_id = property(_get_server_id, set_server_id)
	bind_address = property(_get_bind_address, set_bind_address)
	skip_networking = property(_get_skip_networking, set_skip_networking)
	datadir	 = property(_get_datadir, _set_datadir)
	datadir_default = DEFAULT_DATADIR
	
	
class MySQLDump(object):
	
	host = None
	port = None
	
	def __init__(self, root_user=None, root_password=None):
		self.root_user = root_user or 'root'
		self.root_password = root_password or ''
	
	def create(self, dbname, filename, opts=None):
		opts = opts or []
		LOG.debug('Dumping database %s to %s' % (dbname, filename))
		opts = [MYSQLDUMP_PATH, '-u', self.root_user, '-p'] + opts + ['--databases']
		with open(filename, 'w') as fp: 
			system2(opts + [dbname], stdin=self.root_password, stdout=fp)


class RepicationWatcher(threading.Thread):
	
	
	_state = None
	_client = None
	_master_host = None
	_repl_user = None
	_repl_password = None
	
	
	WATCHER_RUNNING = 'running'
	WATCHER_STOPPED = 'stopped'
	TIMEOUT = 60
	
	
	def __init__(self, client, master_host, repl_user, repl_password):
		super(RepicationWatcher, self).__init__()
		self._client = client
		self.change_master_host(master_host, repl_user, repl_password)
	
	
	def change_master_host(self, host, user, password):
		self.suspend()
		self._master_host = host
		self._repl_user	= user
		self._repl_password = password
		self.resume()
		
		
	def start(self):
		self.resume()
		while True:
			if self._state == self.WATCHER_RUNNING:
				r_status = None
				try:
					r_status = self._client.slave_status()
				except ServiceError, e:
					LOG.error(e)
					
				if not r_status:
					time.sleep(self.TIMEOUT)
				
				elif r_status['Slave_IO_Running'] == 'Yes' and r_status['Slave_SQL_Running'] == 'Yes':
					time.sleep(self.TIMEOUT)
				
				elif r_status and r_status['Slave_SQL_Running'] == 'No' and \
							'Relay log read failure: Could not parse relay log event entry' in r_status['Last_Error']:
					self.repair_relaylog(r_status['Relay_Master_Log_File'], r_status['Exec_Master_Log_Pos'])
					time.sleep(self.TIMEOUT)
				else:
					self.suspend()
					msg = 'Replication is broken. Slave_IO_Running=%s, Slave_SQL_Running=%s, Last_Error=%s' % (
									r_status['Slave_IO_Running'], 
									r_status['Slave_SQL_Running'],
									r_status['Last_Error']
									)
					LOG.error(msg)
	
	def repair_relaylog(self, log_file, log_pos):
		LOG.info('Repairing relay log')
		try:
			self._client.stop_slave()
			self._client.reset_slave()
			self._client.change_master_to(self._master_host, self._repl_user, self._repl_password, log_file, log_pos)
			self._client.sart_slave()
		except BaseException, e:
			self.suspend()
			LOG.error(e)
		else:
			LOG.info('Relay log has been repaired')
		
		
	def resume(self):
		self._state = self.WATCHER_RUNNING
	
	
	def suspend(self):
		self._state = self.WATCHER_STOPPED
	
		
		
class MysqlInitScript(initdv2.ParametrizedInitScript):
	
	socket_file = None
	cli = None
	sgt_pid_path = '/var/run/mysqld/mysqld.pid'
	
	
	@lazy
	def __new__(cls, *args, **kws):
		obj = super(MysqlInitScript, cls).__new__(cls, *args, **kws)
		cls.__init__(obj)
		return obj
			
				
	def __init__(self):
		self.mysql_cli = MySQLClient()
		

		if disttool.is_ubuntu() and disttool.version_info() >= (10, 4):
			initd_script = ('/usr/sbin/service', 'mysql')
		else:
			initd_script = firstmatched(os.path.exists, ('/etc/init.d/mysqld', '/etc/init.d/mysql'))
			
		pid_file = None
		try:
			out = system2("my_print_defaults mysqld", shell=True)
			m = re.search("--pid[-_]file=(.*)", out[0], re.MULTILINE)
			if m:
				pid_file = m.group(1)
			m = re.search("--socket=(.*)", out[0], re.MULTILINE)
			if m:
				self.socket_file = m.group(1)
		except:
			pass
		
		initdv2.ParametrizedInitScript.__init__(self, SERVICE_NAME, 
				initd_script, pid_file, socks=[initdv2.SockParam(MYSQL_DEFAULT_PORT, timeout=3600)])

	
	def _start_stop_reload(self, action):
		''' XXX: Temporary ugly hack (Ubuntu 1004 upstart problem - Job is already running)'''
		try:
			args = [self.initd_script] \
					if isinstance(self.initd_script, basestring) \
					else list(self.initd_script)
			args.append(action) 
			out, err, returncode = system2(args, close_fds=True, preexec_fn=os.setsid)
		except PopenError, e:
			if 'Job is already running' not in str(e):
				raise InitdError("Popen failed with error %s" % (e,))
		
		if action == 'start' and disttool.is_ubuntu() and disttool.version_info() >= (10, 4):
			try:
				LOG.debug('waiting for mysql process')
				wait_until(lambda: MYSQLD_PATH in system2(('ps', '-G', 'mysql', '-o', 'command', '--no-headers'))[0]
							, timeout=10, sleep=1)
			except:
				self._start_stop_reload('restart')
				return True
		
		if self.socks and (action != "stop" and not (action == 'reload' and not self.running)):
			for sock in self.socks:
				wait_sock(sock)
					
		return True
	
			
	def status(self):
		if self.socket_file:
			if os.path.exists(self.socket_file):
				return initdv2.Status.RUNNING if self.mysql_cli.test_connection() else initdv2.Status.NOT_RUNNING
			else:
				return initdv2.Status.NOT_RUNNING
		return initdv2.ParametrizedInitScript.status(self)

	
	def start(self):
		mysql_cnf_err_re = re.compile('Unknown option|ERROR')
		stderr = system2('%s --user=mysql --help' % MYSQLD_PATH, shell=True)[1]
		if re.search(mysql_cnf_err_re, stderr):
			raise Exception('Error in mysql configuration detected. Output:\n%s' % stderr)
		
		if not self.running:
			try:
				LOG.info("Starting mysql")
				initdv2.ParametrizedInitScript.start(self)
				LOG.debug("mysql started")
			except:
				if self._is_sgt_process_exists():
					LOG.warning('MySQL service is running with skip-grant-tables mode.')
				elif not self.running:
					raise
		
		return self._start_stop_reload('start')
	
	def stop(self, reason=None):
		initdv2.ParametrizedInitScript.stop(self)

	
	def restart(self, reason=None):
		initdv2.ParametrizedInitScript.restart(self)
		
	def reload(self, reason=None):
		initdv2.ParametrizedInitScript.reload(self)
		
		
	def _is_sgt_process_exists(self):
		try:
			out = system2(('ps', '-G', 'mysql', '-o', 'command', '--no-headers'))[0]
		except:
			out = None
		return False if not out else MYSQLD_PATH in out and 'skip-grant-tables' in out


	def start_skip_grant_tables(self):
		pid_dir = '/var/run/mysqld/'
		if not os.path.isdir(pid_dir):
			os.makedirs(pid_dir, mode=0755)
			mysql_user	= pwd.getpwnam("mysql")
			os.chown(pid_dir, mysql_user.pw_uid, -1)
		if not self._is_sgt_process_exists():	
			args = [MYSQLD_PATH, '--user=mysql', '--skip-grant-tables']
			p = subprocess.Popen(args, stdin=subprocess.PIPE,stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)
			wait_until(lambda: self._is_sgt_process_exists(), timeout=10, sleep=1)
		wait_until(lambda: self.running, timeout=10, sleep=1)

	
	def stop_skip_grant_tables(self):
		if self._is_sgt_process_exists() and os.path.exists(self.sgt_pid_path):
			sgt_pid = open(self.sgt_pid_path).read().strip()
			if sgt_pid:
				os.kill(int(sgt_pid), signal.SIGTERM)
				wait_until(lambda: not self._is_sgt_process_exists(), timeout=10, sleep=1)
				wait_until(lambda: not self.running, timeout=10, sleep=1)
			else:
				LOG.warning('Unable to stop mysql running with skip-grant-tables. PID not found.')
		


def _add_apparmor_rules(directory):
	if not os.path.exists('/etc/init.d/apparmor'):
		return
	try:
		file = open('/etc/apparmor.d/usr.sbin.mysqld', 'r')
	except IOError, e:
		pass
	else:
		app_rules = file.read()
		file.close()
		if not re.search (directory, app_rules):
			file = open('/etc/apparmor.d/usr.sbin.mysqld', 'w')
			if os.path.isdir(directory):
				app_rules = re.sub(re.compile('(.*)(})([^}]*)', re.S), '\\1\n'+directory+'/ r,\n'+'\\2\\3', app_rules)
				app_rules = re.sub(re.compile('(.*)(})([^}]*)', re.S), '\\1'+directory+'/** rwk,\n'+'\\2\\3', app_rules)
			else:
				app_rules = re.sub(re.compile('(.*)(})([^}]*)', re.S), '\\1\n'+directory+' r,\n'+'\\2\\3', app_rules)
			file.write(app_rules)
			file.close()
			apparmor_initd = initdv2.ParametrizedInitScript('apparmor', '/etc/init.d/apparmor')
			try:
				apparmor_initd.reload()
			except InitdError, e:
				LOG.error('Cannot restart apparmor. %s', e)	

		
initdv2.explore(SERVICE_NAME, MysqlInitScript)
