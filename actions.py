#!/usr/bin/env python3
"""CLI entrypoint for the Omarchy Cryptomator plugin.

Dispatches to the modules that implement each action; see:
  - cli_trust.py        cryptomator-cli identity verification
  - bundle_installer.py downloading/installing the verified bundle
  - vault_unlock.py     locking and password-based unlocking
  - vault_registry.py   vaults.json persistence and vault add/remove/create
  - file_manager.py     file manager window integration
  - process_utils.py    shared process launch/cleanup helpers
"""

import sys
sys.dont_write_bytecode = True

import bundle_installer
import file_manager
import vault_registry
import vault_unlock


def main():
    if len(sys.argv) < 2:
        print("Usage: actions.py <lock|lock-all|unlock|unlock-password|add-vault|remove-vault|setup-bundle|launch|reveal> [arg] [arg2]")
        sys.exit(1)

    action = sys.argv[1].lower()
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    if action == "lock":
        arg2 = sys.argv[3] if len(sys.argv) > 3 else ""
        if not vault_unlock.lock_mount(arg, arg2):
            sys.exit(1)
    elif action == "lock-all":
        vault_unlock.lock_all()
    elif action == "unlock":
        vault_unlock.unlock_vault(arg)
    elif action == "unlock-password":
        mount_point = sys.argv[3] if len(sys.argv) > 3 else ""
        password = sys.stdin.readline().rstrip("\r\n")
        if not vault_unlock.unlock_with_password(arg, mount_point, password):
            sys.exit(1)
    elif action == "setup-bundle":
        if not bundle_installer.setup_bundle():
            sys.exit(1)
    elif action == "add-vault":
        name = sys.argv[3] if len(sys.argv) > 3 else None
        if not vault_registry.add_vault(arg, name):
            sys.exit(1)
    elif action == "create-vault":
        name = sys.argv[3] if len(sys.argv) > 3 else None
        password = sys.stdin.readline().rstrip("\r\n")
        if not vault_registry.create_new_vault(arg, password, name):
            sys.exit(1)
    elif action == "remove-vault":
        if not vault_registry.remove_vault(arg):
            sys.exit(1)
    elif action == "launch":
        file_manager.launch_cryptomator()
    elif action == "reveal":
        file_manager.reveal_in_file_manager(arg)
    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
