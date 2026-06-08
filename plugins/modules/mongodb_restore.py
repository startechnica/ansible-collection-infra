#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2026, firmansyahn
# GNU General Public License v3.0+

DOCUMENTATION = r'''
---
module: mongodb_restore
short_description: Run mongorestore via a temporary Docker container
description:
  - Restores a MongoDB backup using a temporary Docker container sharing the mongos network.
  - Mounts the backup directory from the host into the container.
  - Supports gzip-compressed backups.
  - Can restore specific databases or collections.
version_added: "1.0.0"
author: firmansyahn (@firmansyahn)
options:
  image:
    description: MongoDB Docker image to use for mongorestore.
    required: true
    type: str
  container:
    description: Name of the running mongos container to share network with.
    default: mongodb-mongos
    type: str
  host:
    description: MongoDB host to connect to (inside container network).
    default: 127.0.0.1
    type: str
  port:
    description: MongoDB port.
    default: 27017
    type: int
  username:
    description: MongoDB admin username.
    required: true
    type: str
  password:
    description: MongoDB admin password.
    required: true
    type: str
    no_log: true
  auth_database:
    description: Authentication database.
    default: admin
    type: str
  restore_path:
    description: Path to the mongodump backup directory on the host.
    required: true
    type: str
  uid:
    description: UID to run the container as.
    default: 999
    type: int
  gid:
    description: GID to run the container as.
    default: 999
    type: int
  gzip:
    description: Backup is gzip compressed.
    default: true
    type: bool
  drop:
    description: Drop existing collections before restoring.
    default: false
    type: bool
  db:
    description: Restore a specific database only.
    default: ""
    type: str
  collection:
    description: Restore a specific collection only (requires db).
    default: ""
    type: str
  tls:
    description: Connect with TLS (mongorestore --tls).
    default: false
    type: bool
  tls_host_ca_file:
    description: >
      Path to the CA certificate on the **host** — mounted read-only into
      /tls/ca.pem inside the container.
    default: ""
    type: str
  tls_cert_file:
    description: >
      Path to the client certificate (PEM) on the **host** for
      mutual-TLS authentication.  Mounted read-only into /tls/client.pem.
    default: ""
    type: str
'''

EXAMPLES = r'''
- name: Restore from backup
  mongodb_restore:
    image: docker.io/mongo:8.2.6
    username: admin
    password: "{{ mongodb_admin_password }}"
    restore_path: /opt/mongodb/backups/mongodump_20260420_120000

- name: Restore single database with drop
  mongodb_restore:
    image: docker.io/mongo:8.2.6
    username: admin
    password: "{{ mongodb_admin_password }}"
    restore_path: /opt/mongodb/backups/mongodump_20260420_120000
    db: myapp
    drop: true
'''

RETURN = r'''
restore_path:
  description: The backup path that was restored.
  returned: success
  type: str
stdout:
  description: mongorestore stdout output.
  returned: always
  type: str
stderr:
  description: mongorestore stderr output.
  returned: always
  type: str
'''

import os
import subprocess
from ansible.module_utils.basic import AnsibleModule


def run_cmd(cmd, timeout=600):
    """Run a command and return (rc, stdout, stderr)."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def main():
    module = AnsibleModule(
        argument_spec=dict(
            image=dict(type='str', required=True),
            container=dict(type='str', default='mongodb-mongos'),
            host=dict(type='str', default='127.0.0.1'),
            port=dict(type='int', default=27017),
            username=dict(type='str', required=True),
            password=dict(type='str', required=True, no_log=True),
            auth_database=dict(type='str', default='admin'),
            restore_path=dict(type='str', required=True),
            uid=dict(type='int', default=999),
            gid=dict(type='int', default=999),
            gzip=dict(type='bool', default=True),
            drop=dict(type='bool', default=False),
            db=dict(type='str', default=''),
            collection=dict(type='str', default=''),
            tls=dict(type='bool', default=False),
            tls_host_ca_file=dict(type='str', default=''),
            tls_cert_file=dict(type='str', default=''),
        ),
        supports_check_mode=True,
    )

    image = module.params['image']
    container = module.params['container']
    host = module.params['host']
    port = module.params['port']
    username = module.params['username']
    password = module.params['password']
    auth_database = module.params['auth_database']
    restore_path = module.params['restore_path']
    uid = module.params['uid']
    gid = module.params['gid']
    use_gzip = module.params['gzip']
    drop = module.params['drop']
    db = module.params['db']
    collection = module.params['collection']
    use_tls = module.params['tls']
    tls_host_ca_file = module.params['tls_host_ca_file']
    tls_cert_file = module.params['tls_cert_file']

    result = dict(
        changed=False,
        restore_path=restore_path,
        stdout='',
        stderr='',
    )

    if collection and not db:
        module.fail_json(msg="'db' is required when 'collection' is specified")

    if not os.path.isdir(restore_path):
        module.fail_json(msg="Backup path not found: %s" % restore_path)

    if module.check_mode:
        result['changed'] = True
        result['msg'] = "Would restore from: %s" % restore_path
        module.exit_json(**result)

    # Build mongorestore command
    # Mount the parent directory so the dump subdirectory is accessible
    parent_dir = os.path.dirname(restore_path)
    dump_name = os.path.basename(restore_path)

    restore_cmd = [
        "docker", "run", "--rm",
        "--network", "container:%s" % container,
        "-v", "%s:/backup:ro" % parent_dir,
        "--user", "%d:%d" % (uid, gid),
    ]
    if tls_host_ca_file:
        restore_cmd.extend(["-v", "%s:/tls/ca.pem:ro" % tls_host_ca_file])
    if tls_cert_file:
        restore_cmd.extend(["-v", "%s:/tls/client.pem:ro" % tls_cert_file])
    restore_cmd.extend([
        image,
        "mongorestore",
        "--host", host,
        "--port", str(port),
        "--username", username,
        "--password", password,
        "--authenticationDatabase", auth_database,
    ])

    if use_tls:
        restore_cmd.append("--tls")
        if tls_host_ca_file:
            restore_cmd.extend(["--tlsCAFile", "/tls/ca.pem"])
        if tls_cert_file:
            restore_cmd.extend(["--tlsCertificateKeyFile", "/tls/client.pem"])
    if use_gzip:
        restore_cmd.append("--gzip")
    if drop:
        restore_cmd.append("--drop")
    if db:
        restore_cmd.extend(["--db", db])
    if collection:
        restore_cmd.extend(["--collection", collection])

    # Append the dump path inside the container
    dump_path = "/backup/%s" % dump_name
    if db:
        dump_path = "%s/%s" % (dump_path, db)
    restore_cmd.append(dump_path)

    rc, stdout, stderr = run_cmd(restore_cmd)

    result['stdout'] = stdout
    result['stderr'] = stderr

    if rc != 0:
        module.fail_json(
            msg="mongorestore failed (rc=%d)" % rc,
            **result
        )

    result['changed'] = True
    result['msg'] = "Restored from: %s" % restore_path
    module.exit_json(**result)


if __name__ == '__main__':
    main()
