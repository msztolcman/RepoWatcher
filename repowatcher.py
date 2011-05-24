#!/usr/bin/env python2.6 -tt
# -*- coding: utf-8 -*-

from __future__ import print_function, unicode_literals
import hashlib
import json
import os, os.path
import re
import sys
import subprocess

class RWError (Exception): pass
class RWErrConfig (RWError): pass

class ProjectPath (object):
    def __init__ (self, root_dir=None, create=True):
        if root_dir is None:
            root_dir = '~'

        path = None
        if sys.platform == 'win32':
            path = ('RepoWatcher', )
        elif sys.platform == 'darwin':
            path = ('Library', 'Application Support', 'RepoWatcher', )
        else:
            path = ('.repowatcher', )

        path = os.path.expanduser (os.path.join (root_dir, *path))

        if not path:
            raise RWErrConfig ('Cannot find project path')

        self.path = path

        if create:
            self.create ()

    def create (self):
        if not os.path.exists (self.path):
            return os.makedirs (self.path)

        return True

    def __str__ (self):
        return self.path

    def __unicode__ (self):
        return unicode (self.path)

class RWRepo (object):
    @classmethod
    def make_hash (data):
        return hashlib.sha1 (data['uri']).hexdigest ()

    def __init__ (self, data=None):
        if data is not None:
            self.path       = data['path']
            self.type       = data['type']
            self.name       = data['name']
            self.uri        = data['uri']
            self.last_rev   = data['last_rev']
            self.interval   = data.get ('interval', 600)
            self.last_check = data.get ('last_check', 0)
            self.status     = data['status']
            self.date_add   = data['date_add']
        else:
            self.path       = None
            self.type       = None
            self.name       = None
            self.uri        = None
            self.last_rev   = None
            self.interval   = 600
            self.last_check = 0
            self.status     = None
            self.date_add   = None

    def __repr__ (self):
        return '<{0}: {1}>'.format (self.__class__.__name__, self.uri)

    def __str__ (self):
        return self.uri

    def __unicode__ (self):
        return unicode (self.__str__ ())

def system (*args):
    p = subprocess.Popen (args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate ()
    return p.returncode, stdout, stderr

class RWRepoGit (RWRepo):
    def initialize (self):
        if os.path.exists (self.path):
            raise RWError ('Repository folder already exists: {0}'.format (self.path))

        os.mkdir (self.path)
        os.chdir (self.path)

        ret_code, stdout, stderr = system ('git', 'clone', self.uri)
        if ret_code:
            raise RWerror ('Cloning git repo failed: [{0}] {1}'.format (ret_code, stderr))

        with open (self.path + '_config', 'w') as fh:
            json.dump (dict (
                type       = self.type,
                name       = self.name,
                uri        = self.uri,
                last_rev   = self.last_rev,
                interval   = self.interval,
                last_check = self.last_check,
                status     = self.status,
                date_add   = self.date_add,
            ), fh)

    def update (self):
        pass

    def read (self):
        pass

    def read_last (self):
        pass

    def remove (self):
        pass

class RWRepoSvn (RWRepo):
    pass

repos_types = dict (
    git = RWRepoGit,
    svn = RWRepoSvn,
)
def repo_factory (data):
    if data['type'] not in repos_types:
        raise KeyError ('Unknown repository type: {0}'.format (data['type']))

    return repos_types[data['type']] (data)

class RWRepos (object):
    def __init__ (self, path):
        self.path       = path

        if not os.path.isdir (path):
            raise RWError ('Wrong repo path: {0}'.format (path))

    def __iter__ (self):
        objs = os.listdir (self.path)
        objs.sort ()
        for obj in objs:
            path = os.path.join (self.path, obj)
            if len (obj) == 40 and os.path.isdir (path) and os.path.isfile (path + '_config'):
                try:
                    with open (path + '_config', 'r') as fh:
                        cfg = json.load (fh)
                except Exception as e:
                    print ('err:', e)
                    continue

                cfg['path'] = path
                cfg['hash'] = obj
                yield repo_factory (cfg)

class RWActions:
    def action_start (argv):
        """ starts daemon """
        pass

    def action_stop (argv):
        """ stops daemon """
        pass

    def action_enable (argv):
        """ enable updates for repo """
        pass

    def action_disable (argv):
        """ disable updates for repo """
        pass

    def action_add (argv):
        """ add new repo """
        pass

    def action_rm (argv):
        """ remove repo """
        pass

    def action_clear (argv):
        """ remove all repos """
        pass

    def action_list (argv):
        """ list repos names and uris"""
        pass

    def action_reload (argv):
        """ reload daemon """
        pass

    def action_status (argv):
        """ show daemon status """
        pass

    def action_info (argv):
        """ show repository info """
        pass

def main ():
    project_path = ProjectPath (create=True)
    r = RWRepos (unicode (project_path))
    for repo in r:
        if repo.status == 'disabled':
            continue

        repo.initialize ()
#         print ('REPO', repr (repo))

if __name__ == '__main__':
    main ()
