from __future__ import with_statement
'''
Created on Nov 25, 2011

@author: marat
'''
from __future__ import with_statement

import threading

from scalarizr import handlers, rpc
from scalarizr import storage2


class StorageAPI(object):

    error_messages = {
            'empty': "'%s' can't be blank",
            'invalid': "'%s' is invalid, '%s' expected"
    }

    @rpc.service_method
    def create(self, volume=None, mkfs=False, mount=False, fstab=False, async=False):
        '''
        :type volume: dict
        :param volume: Volume configuration object

        :type mkfs: bool
        :param mkfs: Whether create filesystem on volume device.
                Error will be raised if existed filesystem detected.

        :type mount: bool
        :param mount: Whether mount volume device.
                Non blank `mpoint` in volume configuration required

        :type fstab: bool
        :param fstab: Whether add device to /etc/fstab

        :type async: bool
        :param async: Execute method in separate thread and report status
                        with Operation/Steps mechanism

        :rtype: dict|string
        '''
        self._check_invalid(volume, 'volume', dict)

        def do_create():
            vol = storage2.volume(volume)
            vol.ensure(mkfs=mkfs, mount=mount, fstab=fstab)
            return dict(vol)


        if async:
            txt = 'Create volume'
            op = handlers.operation(name=txt)
            def block():
                op.define()
                with op.phase(txt):
                    with op.step(txt):
                        data = do_create()
                op.ok(data=data)
            threading.Thread(target=block).start()
            return op.id

        else:
            return do_create()


    @rpc.service_method
    def snapshot(self, volume=None, description=None, tags=None, async=False):
        '''
        :type volume: dict
        :param volume: Volume configuration object

        :type description: string
        :param description: Snapshot description

        :type tags: dict
        :param tags: Key-value tagging. Only 'ebs' and 'gce_persistent'
                volume types support it

        :type async: bool
        :param async: Execute method in separate thread and report status
                        with Operation/Steps mechanism
        '''
        self._check_invalid(volume, 'volume', dict)
        self._check_empty(volume.get('id'), 'volume.id')
        if description:
            self._check_invalid(description, 'description', basestring)
        if tags:
            self._check_invalid(tags, 'tags', dict)

        def do_snapshot():
            vol = storage2.volume(volume)
            vol.ensure()
            snap = vol.snapshot(description=description, tags=tags)
            return dict(snap)

        if async:
            txt = 'Create snapshot'
            op = handlers.operation(name=txt)
            def block():
                op.define()
                with op.phase(txt):
                    with op.step(txt):
                        data = do_snapshot()
                op.ok(data=data)
            threading.Thread(target=block).start()
            return op.id

        else:
            return do_snapshot()


    @rpc.service_method
    def detach(self, volume=None, force=False, async=False, **kwds):
        '''
        :type volume: dict
        :param volume: Volume configuration object

        :type force: bool
        :param force: More aggressive.
                - 'ebs' will pass it to DetachVolume
                - 'raid' will pass it to underlying disks

        :type async: bool
        :param async: Execute method in separate thread and report status
                        with Operation/Steps mechanism
        '''
        self._check_invalid(volume, 'volume', dict)
        self._check_empty(volume.get('id'), 'volume.id')

        def do_detach():
            vol = storage2.volume(volume)
            vol.ensure()
            vol.detach(force=force, **kwds)
            return dict(vol)

        if async:
            txt = 'Detach volume'
            op = handlers.operation(name=txt)
            def block():
                op.define()
                with op.phase(txt):
                    with op.step(txt):
                        data = do_detach()
                op.ok(data=data)
            threading.Thread(target=block).start()
            return op.id

        else:
            return do_detach()


    @rpc.service_method
    def destroy(self, volume, force=False, async=False, **kwds):
        '''
        :type volume: dict
        :param volume: Volume configuration object

        :type force: bool
        :param force: More aggressive.
                - 'ebs' will pass it to DetachVolume
                - 'raid' will pass it to underlying disks

        :type async: bool
        :param async: Execute method in separate thread and report status
                        with Operation/Steps mechanism
        '''
        self._check_invalid(volume, 'volume', dict)
        self._check_empty(volume.get('id'), 'volume.id')

        def do_destroy():
            vol = storage2.volume(volume)
            vol.ensure()
            vol.detach(force=force, **kwds)
            return dict(vol)

        if async:
            txt = 'Destroy volume'
            op = handlers.operation(name=txt)
            def block():
                op.define()
                with op.phase(txt):
                    with op.step(txt):
                        data = do_destroy()
                op.ok(data=data)
            threading.Thread(target=block).start()
            return op.id

        else:
            return do_destroy()


    @rpc.service_method
    def grow(self, volume, growth, async=False):
        self._check_invalid(volume, 'volume', dict)
        self._check_empty(volume.get('id'), 'volume.id')

        def do_grow():
            vol = storage2.volume(volume)
            growed_vol = vol.grow(**growth)
            return dict(growed_vol)

        if async:
            txt = 'Grow volume'
            op = handlers.operation(name=txt)
            def block():
                op.define()
                with op.phase(txt):
                    with op.step(txt):
                        data = do_grow()
                op.ok(data=data)
            threading.Thread(target=block).start()
            return op.id

        else:
            return do_grow()


    @rpc.service_method
    def replace_raid_disk(self, volume, index, disk, async=False):
        self._check_invalid(volume, 'volume', dict)
        self._check_invalid(volume, 'index', int)
        self._check_empty(volume.get('id'), 'volume.id')

        def do_replace_raid_disk():
            vol = storage2.volume(volume)
            vol.replace_disk(index, disk)
            return dict(vol)

        if async:
            txt = 'Replace RAID disk'
            op = handlers.operation(name=txt)
            def block():
                op.define()
                with op.phase(txt):
                    with op.step(txt):
                        data = do_replace_raid_disk()
                op.ok(data=data)
            threading.Thread(target=block).start()
            return op.id

        else:
            return do_replace_raid_disk()


    def _check_invalid(self, param, name, type_):
        assert isinstance(param, type_), self.error_messages['invalid'] % (name, type_)

    def _check_empty(self, param, name):
        assert param, self.error_messages['empty'] % name
