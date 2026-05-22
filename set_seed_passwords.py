"""
Sets passwords for all RMM seed users.
Run this once after bench migrate on a fresh install.

Usage:
    python set_seed_passwords.py <site>

Example:
    python set_seed_passwords.py mysite.localhost
"""

import subprocess
import sys

PASSWORD = "Rmm@12345"

SEED_USERS = [
    "rmm_team_lead@yopmail.com",
    "requestor@yopmail.com",
    "requestor_2@yopmail.com",
    "review_agent@yopmail.com",
    "review_agent_james@yopmail.com",
    "medical_verification_agent@yopmail.com",
]


def set_passwords(site: str) -> None:
    print(f"Setting passwords for seed users on site: {site}\n")
    for user in SEED_USERS:
        result = subprocess.run(
            ["bench", "--site", site, "set-password", user, PASSWORD],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"  OK  {user}")
        else:
            print(f"  FAIL  {user}")
            if result.stderr:
                print(f"        {result.stderr.strip()}")
    print(f"\nDone. All users can now log in with password: {PASSWORD}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python set_seed_passwords.py <site>")
        sys.exit(1)
    set_passwords(sys.argv[1])
