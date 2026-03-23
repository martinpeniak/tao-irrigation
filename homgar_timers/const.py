DOMAIN = "homgar_timers"

# API endpoints
HOMGAR_BASE_URL = "https://region3.homgarus.com"
HOMGAR_LOGIN_PATH = "/auth/basic/app/login"
HOMGAR_HOMES_PATH = "/app/member/appHome/list"
HOMGAR_DEVICES_PATH = "/app/device/getDeviceByHid"
HOMGAR_DEVICE_STATUS_PATH = "/app/device/getDeviceStatus"

# Timer model supported
TIMER_MODEL = "HTV0540FRF"

# HomGar stop timestamps use a custom epoch starting on 2012-12-20.
HOMGAR_EPOCH_OFFSET = 1355964032

# Default duration in seconds (10 minutes)
DEFAULT_DURATION_SECONDS = 600
STATE_POLL_INTERVAL_SECONDS = 30

# configuration.yaml keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_AREA_CODE = "area_code"

# D01 payload byte offsets (after stripping "11#" prefix and hex-decoding)
# byte[6]:    observed running flags include 0x20|zone and 0x40|zone
# byte[24:28]: LE uint32 = stop timestamp in HomGar epoch seconds
# byte[42:44]: LE uint16 = duration in seconds
ZONE_RUNNING_FLAGS = (0x20, 0x40)
ZONE_FLAG_BYTE_OFFSET = 6
STOP_TS_BYTE_OFFSET = 24
DURATION_BYTE_OFFSET = 42
