#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2026, firmansyahn
# GNU General Public License v3.0+

DOCUMENTATION = r'''
---
module: mongodb_backup
short_description: Run mongodump via a temporary Docker container
description:
  - Runs mongodump using a temporary Docker container sharing the mongos network.
  - Mounts the backup directory from the host into the container.
  - Supports gzip compression.
  - Optionally prunes old backups by retention days.
version_added: "1.0.0"
author: firmansyahn (@firmansyahn)
options:
  image:
    description: MongoDB Docker image to use for mongodump.
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
  mongodb_backup_dir:
    description: Host directory to store backups.
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
    description: Compress backup with gzip.
    default: true
    type: bool
  db:
    description: Backup a specific database only.
    default: ""
    type: str
  collection:
    description: Backup a specific collection only (requires db).
    default: ""
    type: str
  retain_days:
    description: Delete backups older than this many days. 0 to disable pruning.
    default: 0
    type: int
  oplog:
    description: >
      Capture the oplog during the dump (mongodump --oplog). Required for
      point-in-time recovery via pitr.yml. Only valid for replica set /
      sharded cluster dumps (not single-db --db dumps).
    default: false
    type: bool
  tls:
    description: Connect with TLS (mongodump --tls).
    default: false
    type: bool
  tls_ca_file:
    description: >
      Path to the CA certificate **inside the container** for TLS verification.
      Mount the host file via tls_host_ca_file.
    default: ""
    type: str
  tls_host_ca_file:
    description: >
      Path to the CA certificate on the **host** — mounted read-only into
      /tls/ca.pem inside the container.  Takes precedence over tls_ca_file
      when set.
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
- name: Backup MongoDB cluster
  mongodb_backup:
    image: docker.io/mongo:8.2.6
    username: admin
    password: "{{ mongodb_admin_password }}"
    mongodb_backup_dir: /opt/mongodb/backups

- name: Backup with 7-day retention
  mongodb_backup:
    image: docker.io/mongo:8.2.6
    username: admin
    password: "{{ mongodb_admin_password }}"
    mongodb_backup_dir: /opt/mongodb/backups
    retain_days: 7

- name: Backup single database
  mongodb_backup:
    image: docker.io/mongo:8.2.6
    username: admin
    password: "{{ mongodb_admin_password }}"
    mongodb_backup_dir: /opt/mongodb/backups
    db: myapp
'''

RETURN = r'''
backup_path:
  description: Full path to the backup directory on the host.
  returned: success
  type: str
backup_name:
  description: Name of the backup directory.
  returned: success
  type: str
pruned:
  description: Number of old backups pruned.
  returned: success
  type: int
'''

import glob
import os
import subprocess
import time
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
            mongodb_backup_dir=dict(type='str', required=True),
            uid=dict(type='int', default=999),
            gid=dict(type='int', default=999),
            gzip=dict(type='bool', default=True),
            db=dict(type='str', default=''),
            collection=dict(type='str', default=''),
            retain_days=dict(type='int', default=0),
            oplog=dict(type='bool', default=False),
            tls=dict(type='bool', default=False),
            tls_ca_file=dict(type='str', default=''),
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
    mongodb_backup_dir = module.params['mongodb_backup_dir']
    uid = module.params['uid']
    gid = module.params['gid']
    use_gzip = module.params['gzip']
    db = module.params['db']
    collection = module.params['collection']
    retain_days = module.params['retain_days']
    use_oplog = module.params['oplog']
    use_tls = module.params['tls']
    tls_ca_file = module.params['tls_ca_file']
    tls_host_ca_file = module.params['tls_host_ca_file']
    tls_cert_file = module.params['tls_cert_file']

    backup_name = "mongodump_%s" % time.strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(mongodb_backup_dir, backup_name)

    result = dict(
        changed=False,
        backup_path=backup_path,
        backup_name=backup_name,
        pruned=0,
    )

    if collection and not db:
        module.fail_json(msg="'db' is required when 'collection' is specified")
    if use_oplog and db:
        module.fail_json(msg="'oplog' and 'db' are mutually exclusive — oplog requires a full cluster dump")

    # Ensure backup directory exists
    if not os.path.isdir(mongodb_backup_dir):
        if module.check_mode:
            result['changed'] = True
            module.exit_json(**result)
        os.makedirs(mongodb_backup_dir, mode=0o750, exist_ok=True)
        os.chown(mongodb_backup_dir, uid, gid)

    if module.check_mode:
        result['changed'] = True
        result['msg'] = "Would create backup: %s" % backup_path
        module.exit_json(**result)

    # Resolve TLS paths: host-side file overrides the in-container path.
    effective_ca = "/tls/ca.pem" if tls_host_ca_file else tls_ca_file
    effective_cert = "/tls/client.pem" if tls_cert_file else ""

    # Build mongodump command
    dump_cmd = [
        "docker", "run", "--rm",
        "--network", "container:%s" % container,
        "-v", "%s:/backup" % mongodb_backup_dir,
        "--user", "%d:%d" % (uid, gid),
    ]
    if tls_host_ca_file:
        dump_cmd.extend(["-v", "%s:/tls/ca.pem:ro" % tls_host_ca_file])
    if tls_cert_file:
        dump_cmd.extend(["-v", "%s:/tls/client.pem:ro" % tls_cert_file])
    dump_cmd.extend([
        image,
        "mongodump",
        "--host", host,
        "--port", str(port),
        "--username", username,
        "--password", password,
        "--authenticationDatabase", auth_database,
        "--out", "/backup/%s" % backup_name,
    ])

    if use_gzip:
        dump_cmd.append("--gzip")
    if use_oplog:
        dump_cmd.append("--oplog")
    if use_tls:
        dump_cmd.append("--tls")
        if effective_ca:
            dump_cmd.extend(["--tlsCAFile", effective_ca])
        if effective_cert:
            dump_cmd.extend(["--tlsCertificateKeyFile", effective_cert])
    if db:
        dump_cmd.extend(["--db", db])
    if collection:
        dump_cmd.extend(["--collection", collection])

    rc, stdout, stderr = run_cmd(dump_cmd)
    if rc != 0:
        module.fail_json(
            msg="mongodump failed (rc=%d)" % rc,
            stdout=stdout,
            stderr=stderr,
        )

    result['changed'] = True

    # Prune old backups
    if retain_days > 0:
        cutoff = time.time() - (retain_days * 86400)
        pruned = 0
        for entry in glob.glob(os.path.join(mongodb_backup_dir, "mongodump_*")):
            if os.path.isdir(entry) and os.path.getmtime(entry) < cutoff:
                import shutil
                shutil.rmtree(entry)
                pruned += 1
        result['pruned'] = pruned

    result['msg'] = "Backup created: %s" % backup_path
    module.exit_json(**result)


if __name__ == '__main__':
    main()
