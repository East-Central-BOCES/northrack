# **East Central BOCES**

### _North Rack Initialization_

Remember. This is in public so don't go putting any secrets here!

## Rocky 9.9 migration

The target is **Gluster plus oVirt on Rocky Linux 9.9 x86_64**. See the
[migration plan and repository investigation](docs/rocky9-migration.md) and
[disposable-container package probe](tools/check-rocky9-packages.sh).

The top-level deployment scripts are the historical Rocky 8 implementation.
They are not yet updated or validated for Rocky 9.9. The migration work continues
in this repository.

The new entry point is `provision.sh`; the historical scripts above are not part
of its execution path. It requires a role configuration, an accepted RPM cache,
and the independently recorded SHA-256 of that cache's manifest:

```sh
sudo bash provision.sh --config examples/nr99-node1.json \
  --rpm-set /var/lib/nr99/host-rpms-002 --manifest-sha256 ACCEPTED_MANIFEST_SHA256 \
  --format-data-disks --check
```

Remove `--check` to apply. Formatting additionally requires `allow_format: true`
in the configuration and stable disk identities. All selected disks are checked
before any are formatted; physical disks require verified-backup evidence.
Success is recorded only after the playbook completes. See the
[north31 lab instructions and ordered acceptance gates](lab/README.md).
