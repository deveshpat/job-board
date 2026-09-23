# Encrypted job-board data

Written by the job board (web app, Mac app, and the scheduled GitHub Action). Everything except
keys.json is AES-256-GCM ciphertext; keys.json holds the data key wrapped by your passphrase/passkeys.
