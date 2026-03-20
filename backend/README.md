# NetOps AI V1 Backend

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

## Test

```bash
pytest -q
```

## One-off Device Diagnosis

Run a single diagnosis directly from environment variables. Do not place the password in the repository.

```bash
export DEVICE_HOST=192.168.0.88
export DEVICE_USERNAME=zhangwei
export DEVICE_PASSWORD
read -s DEVICE_PASSWORD
export DEVICE_PROTOCOL=ssh
export DEVICE_TYPE=huawei
export VENDOR=huawei
export AUTOMATION_LEVEL=assisted
export DIAG_MESSAGE='Please diagnose connectivity, interfaces, and routing.'
python scripts/run_device_diag.py
```

Optional: automatically approve high-risk commands for one-off run:

```bash
export AUTO_APPROVE_HIGH_RISK=true
```

Safety note: keep `AUTO_APPROVE_HIGH_RISK` off by default, and only enable it for short-lived, controlled runs. Do not use it for unattended production changes.

Optional connectivity pre-check (SSH):

```bash
python scripts/check_ssh_connection.py --protocol ssh --device-type huawei --host "$DEVICE_HOST" --username "$DEVICE_USERNAME" --password "$DEVICE_PASSWORD" --port 22
```
