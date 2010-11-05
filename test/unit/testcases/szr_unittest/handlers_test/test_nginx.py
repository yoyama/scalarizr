'''
Created on 19.01.2010

@author: Dmytro Korsakov
'''
import unittest
from scalarizr.bus import bus
from scalarizr.handlers import nginx
import os
from scalarizr.util.filetool import read_file, write_file
from szr_unittest import RESOURCE_PATH
from scalarizr.config import ScalarizrCnf

class Message:
	local_ip = '8.8.8.8'
	
				
class TestNginx(unittest.TestCase):
	

	def setUp(self):
		
		bus.etc_path = os.path.join(RESOURCE_PATH, 'etc')
		cnf = ScalarizrCnf(bus.etc_path)
		cnf.load_ini('www')
		bus.cnf = cnf
		self._cnf = bus.cnf
		
		bus.base_path = os.path.realpath(RESOURCE_PATH + "/../../..")
		bus.share_path = os.path.join(bus.base_path, 'share')
		
		bus.queryenv_service = _EmptyQueryEnv()
		bus.define_events("before_host_down", "init")


	def test_on_VhostReconfigure(self):
		https_include_path = "/etc/nginx/https.include"
		_queryenv = bus.queryenv_service = _QueryEnv()

		https_include = read_file(https_include_path)
		
		cert_path =  self._cnf.key_path("https.crt")
		pk_path = self._cnf.key_path("https.key")
		
		cert = read_file(cert_path)
		pk = read_file(pk_path)
		
		print 'Cleaning..'
		for file in (https_include_path, cert_path, pk_path):
			if os.path.exists(file):
				os.remove(file)
				print 'File %s deleted.' % file 
		
		n = nginx.NginxHandler()
		n.on_VhostReconfigure(None)
				
		self.assertTrue(os.path.isfile(https_include_path))
		self.assertTrue(os.path.isfile(cert_path))
		self.assertTrue(os.path.isfile(pk_path))
				
		self.assertEquals(_queryenv.list_virtual_hosts()[0]['raw']+'\n', https_include)
		self.assertEquals(_queryenv.get_https_certificate()[0], cert)
		self.assertEquals(_queryenv.get_https_certificate()[1], pk)
		
		
	def test_creating_vhosts(self):
		config = bus.config
		sect_name = nginx.CNF_SECTION
		nginx_incl = "/etc/nginx/app-servers.include"
		config.set(sect_name, "app_include_path",nginx_incl)
		if os.path.exists(nginx_incl):
			os.remove(nginx_incl)
		
		n = nginx.NginxHandler()
		n._reload_upstream()
		
		self.assertTrue(os.path.exists(nginx_incl))
	

	def test_on_BeforeHostTerminate(self):
		config = bus.config
		include_path = "/etc/nginx/app-servers.include"
		config.set('www','app_include_path',include_path)
		data = """\nupstream backend {\n\tip_hash;\n\n\t\tserver 8.8.8.8:80;\n\n}"""
		write_file(include_path, data)	
		
		n = nginx.NginxHandler()
		n.on_BeforeHostTerminate(Message)
		
		new_data = read_file(include_path)
		self.assertEquals(new_data,"""\nupstream backend {\n\tip_hash;\n\n\tserver\t127.0.0.1:80;\n}\n""")
		
class _Bunch(dict):
			__getattr__, __setattr__ = dict.get, dict.__setitem__
			
class _QueryEnv:
	
	def list_roles(self, behaviour):
		return [_Bunch(
			behaviour = ["app"],
			name = "nginx",
			hosts = [_Bunch(index='1',replication_master="1",internal_ip="8.8.8.8",external_ip="192.168.1.93")]
			)]
		
	def list_virtual_hosts(self, name = None, https=False):
		return [_Bunch(hostname = "test1.net",
						type = "nginx",
						raw= """http {
  server {
    listen          80 default;
    server_name     _;
    access_log      logs/default.access.log main;
 
    server_name_in_redirect  off;
 
    index index.html;
    root  /var/www/default/htdocs;
  }
}""",							
			https = True,
			)]		
		
	def get_https_certificate(self):
		return ("MIICWjCCAhigAwIBAgIESPX5.....1myoZSPFYXZ3AA9kwc4uOwhN","MIICWjCCAhigAwIBAgIESPX5.....1myoZSPFYXZ3AA9kwc4uOwhN")
		

class _EmptyQueryEnv:
	def list_roles(self,behaviour):
		return []
			
if __name__ == "__main__":
	unittest.main()