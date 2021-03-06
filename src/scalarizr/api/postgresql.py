'''
Created on Feb 25, 2013

@author: uty
'''

import re
from scalarizr.util import PopenError
from scalarizr.util.cryptotool import pwgen
from scalarizr.services import postgresql as postgresql_svc
from scalarizr import rpc


OPT_REPLICATION_MASTER = postgresql_svc.OPT_REPLICATION_MASTER
__postgresql__ = postgresql_svc.__postgresql__


class PostgreSQLAPI(object):

    replication_status_query = '''SELECT
    CASE WHEN pg_last_xlog_receive_location() = pg_last_xlog_replay_location()
    THEN 0
    ELSE EXTRACT (EPOCH FROM now() - pg_last_xact_replay_timestamp()) END
    AS xlog_delay;
    '''

    @rpc.service_method
    def reset_password(self, new_password=None):
        """ Reset password for PostgreSQL user 'scalr_master'. Return new password """
        if not new_password:
            new_password = pwgen(10)
        pg = postgresql_svc.PostgreSql()
        if pg.master_user.exists():
            pg.master_user.change_role_password(new_password)
            pg.master_user.change_system_password(new_password)
        else:
            pg.create_linux_user(pg.master_user.name, new_password)
            pg.create_pg_role(pg.master_user.name,
                                new_password,
                                super=True,
                                force=False)
        return new_password

    def _parse_query_out(self, out):
        '''
        Parses xlog_delay or error string from strings like:
         log_delay
        -----------
                 034
        (1 row)

        and:
        ERROR:  function pg_last_xact_replay_timesxtamp() does not exist
        LINE 1: select pg_last_xact_replay_timesxtamp() as not_modified_sinc...
                       ^
        HINT:  No function matches the given name and argument...

        '''
        result = {'error': None, 'xlog_delay': None}
        error_match = re.search(r'ERROR:.*?\n', out)
        if error_match:
            result['error'] = error_match.group()
            return result

        diff_match = re.search(r'xlog_delay.+-\n *\d+', out, re.DOTALL)
        if not diff_match:
            #if no error and query returns nothing
            return result

        result['xlog_delay'] = diff_match.group().splitlines()[-1].strip()
        return result

    @rpc.service_method
    def replication_status(self):
        psql = postgresql_svc.PSQL()
        try:
            query_out = psql.execute(self.replication_status_query)
        except PopenError, e:
            if 'function pg_last_xact_replay_timestamp() does not exist' in str(e):
                raise BaseException('This version of PostgreSQL server does not support replication status')
            else:
                raise e
        query_result = self._parse_query_out(query_out)

        is_master = int(__postgresql__[OPT_REPLICATION_MASTER])

        if not query_result['xlog_delay']:
            if is_master:
                return {'master': {'status': 'up'}}
            return {'slave': {'status': 'down',
                              'error': query_result['error']}}
        return {'slave': {'status': 'up',
                          'xlog_delay': query_result['xlog_delay']}}
