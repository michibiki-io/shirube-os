"""Erase free ext4 blocks offline without allocating a volume-sized zero file."""


def erase_ext4_free_space(guestfs):
    """The caller attaches only its fresh task-owned candidate disk.

    Unmounting and removing the journal precede zerofree. Unlike trimming alone,
    zerofree overwrites nonzero unallocated blocks before the discard operation.
    A failure aborts candidate generation; there is no alternate erasure mode.
    """
    device = '/dev/sda3'
    if guestfs.mountpoints():
        raise ValueError('Free-space erasure requires unmounted filesystems')
    if guestfs.vfs_type(device) != 'ext4':
        raise ValueError('Free-space erasure requires the expected ext4 filesystem')
    features = guestfs.tune2fs_l(device).get('Filesystem features', '')
    if 'inline_data' in features:
        raise ValueError('Inline inode data requires additional erasure validation')
    guestfs.available(['zerofree', 'fstrim'])
    guestfs.debug('sh', ['e2fsck -fy '+device])
    guestfs.debug('sh', ['tune2fs -O ^has_journal '+device])
    guestfs.zerofree(device)
    guestfs.mount(device, '/')
    guestfs.fstrim('/')
    guestfs.umount_all()
    guestfs.debug('sh', ['tune2fs -j '+device])
    guestfs.debug('sh', ['e2fsck -fy '+device])
