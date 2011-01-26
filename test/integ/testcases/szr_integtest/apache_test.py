'''
Created on Jan 26th 2011

@author: Dmitry Korsakov
'''

import unittest
import string
import logging
import time

from szr_integtest 				 import get_selenium
from szr_integtest_libs.datapvd  import DataProvider
from szr_integtest_libs.scalrctl import FarmUI
from szr_integtest_libs.ssh_tool import execute
from szr_integtest_libs.scalrctl import	ScalrCtl
from szr_integtest.nginx_test	 import VirtualTest
from szr_integtest.nginx_test	 import TerminateTest

from scalarizr.util import ping_socket
from scalarizr.util import system2


class StartupTest(VirtualTest):

	def test_startup(self):
		self.logger.info("Startup Test")
		
		self.app_pvd.wait_for_hostup(self.server)		
		
		ping_socket(self.server.public_ip, 80, exc_str='Apache is not running')
		self.logger.info("Nginx is running on 80 port")
		
		self.logger.info("Getting default page from app instance")
		out = system2("curl %s:80" % self.server.public_ip , shell=True)[0]
		print out
		if -1 == string.find(out, 'Scalr farm configured succesfully'):
			raise Exception('Nginx is not serving dummy page')
		self.logger.info('Nginx is serving proper dummy page')
		
		self.logger.info("Startup test is finished.")


class RestartTest(VirtualTest):
	app_pvd = None
	
	def test_restart(self):
		self.logger.info("Restart Test")
		
		self.app_pvd.wait_for_hostup(self.server)
		
		self.logger.info("Logging on app through ssh")
		ssh = self.server.ssh()
		
		self.logger.info("Enabling debug log")
		execute(ssh, 'cp /etc/scalr/logging-debug.ini /etc/scalr/logging.ini', 15)
		
		#temporary solution `cause restart triggers "address already in use" error
		self.logger.info("Restarting scalarizr")
		execute(ssh, '/etc/init.d/scalarizr stop', 15)
		time.sleep(10)
		self.logger.info(execute(ssh, 'lsof -i TCP:8013', 15))
		execute(ssh, '/etc/init.d/scalarizr start', 15)
		
		# Check that upstream was reloaded
		self.logger.info("getting log from server")
		log = self.server.log.tail()
		
		log.expect('Scalarizr terminated')
		self.logger.info('Scalarizr terminated')
		
		log.expect('Starting scalarizr')
		self.logger.info('Scalarizr started')

		log.expect('Requesting virtual hosts list')
		log.expect('Virtual hosts list obtained')
		
		self.logger.info('Virtual host list reloaded')
		self.logger.info("Restart test is finished.")		


class HttpTest(VirtualTest):
	
	def test_http(self):
		self.logger.info("HTTP Test")
		domain = 'dima4test.com'
		role_name = self.app_pvd.role_name
		
		ssh = self.server.ssh()
		execute(ssh, "mkdir /var/www/%s" % domain, 15)
		execute(ssh, "echo 'test_http' > /var/www/%s/index.html" % domain, 15)
		
		farmui = FarmUI(get_selenium())
		farmui.configure_vhost(domain, role_name)
		upstream_log = self.server.log.head()
		upstream_log.expect("VhostReconfigure")
		self.logger.info('got VhostReconfigure')
		
		out = system2("curl %s:80" % self.server.public_ip , shell=True)[0]
		print out
		if -1 == string.find(out, 'test_http'):
			raise Exception('Apache is not serving index.html')
		self.logger.info('Apache is serving proper index.html')
		
		self.logger.info("HTTP test is finished.")
	
	
class HttpsTest(VirtualTest):
	
	def test_https(self):
		self.logger.info("HTTPS Test")
		domain = 'dima4test.com'
		role_name = self.app_pvd.role_name
		
		farmui = FarmUI(get_selenium())
		farmui.configure_vhost_ssl(domain, role_name)
		
		upstream_log = self.server.log.head()
		upstream_log.expect("VhostReconfigure")
		self.logger.info('got VhostReconfigure')
		
		out = system2('/usr/bin/openssl s_client -connect %s:443' % self.server.public_ip)
		if -1 == string.find(out, '1 s:/'):
			raise Exception('CA file probably ignored or simply does not exist')
		self.logger.info('cert OK.')
		
		self.logger.info("HTTPS test is finished.")


class RebundleTest(VirtualTest):
	server = None
	app_pvd = None
	
	def test_rebundle(self):
		self.logger.info("Rebundle Test")
		self.logger.debug("Waiting for HostUp on balancer")
		self.app_pvd.wait_for_hostup(self.server)
		
		self.logger.debug("getting log from balancer")
		reader = self.server.log.tail()
		farmui = self.app_pvd.farmui
		self.logger.debug("Starting bundle process")
		new_role_name = farmui.run_bundle(self.server.scalr_id)
		
		scalrctl = ScalrCtl(self.app_pvd.farm_id)
		scalrctl.exec_cronjob('BundleTasksManager')
		
		self.logger.debug("Waiting for message 'Rebundle'")
		reader.expect("Received message 'Rebundle'", 60)
		self.logger.debug("Received message 'Rebundle'")
		
		reader.expect("Message 'RebundleResult' delivered", 240)
		self.logger.debug("Received message 'RebundleResult'")
		
		rebundle_res = self.server.get_message(message_name='RebundleResult')
		self.assertTrue('<status>ok' in rebundle_res)
		
		scalrctl.exec_cronjob('ScalarizrMessaging')
		scalrctl.exec_cronjob('BundleTasksManager')
		scalrctl.exec_cronjob('BundleTasksManager')
		
		self.app_pvd.terminate()
		
		self.logger.debug("Running all tests agaen")
		self.suite._tests.remove(self)
		self.suite.run_tests(new_role_name)
		
		
class ApacheSuite(unittest.TestSuite):
	
	def __init__(self, tests=(), role_name=None):
		unittest.TestSuite.__init__(self, tests)
		self.logger = logging.getLogger(__name__)
		self.run_tests(role_name)
		
	def run_tests(self, role_name=None):
		self.logger.info("Getting servers, configuring farm")
		kwargs = {'behaviour' : 'www', 'arch' : 'x86_64'}
		if role_name:
			kwargs.update({'role_name': role_name})
		app_pvd = DataProvider(**kwargs)
		self.logger.info("Farm configured")
		
		self.logger.info("Starting load balancer")
		server = app_pvd.server()
		self.logger.info("Load balancer started")
		
		startup = StartupTest('test_startup', app_pvd=app_pvd, server=server)
		restart = RestartTest('test_restart', app_pvd=app_pvd, server=server)
		http = HttpTest('test_http', app_pvd=app_pvd, server=server)
		https = HttpsTest('test_https', app_pvd=app_pvd, server=server)
		rebundle = RebundleTest('test_rebundle', app_pvd=app_pvd, server=server, suite = self)
		terminate = TerminateTest('test_terminate', pvd=app_pvd)
		
		self.addTest(startup)
		self.addTest(restart)
		self.addTest(http)
		self.addTest(https)
		self.addTest(rebundle)
		self.addTest(terminate)
		
		self.logger.info("Number of testes: %s. Starting tests." % self.countTestCases())
		
		
if __name__ == "__main__":
	unittest.main()	