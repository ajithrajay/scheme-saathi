"""Run locally and copy output into the host's secret environment settings."""
import secrets
from cryptography.fernet import Fernet
print('SCHEMESAATHI_OWNER_KEY='+secrets.token_urlsafe(48))
print('SCHEMESAATHI_ENCRYPTION_KEY='+Fernet.generate_key().decode())
print('\nKeep these private and back up the encryption key separately from the database.')
