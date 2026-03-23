DOMAIN = "homgar_timers"

# API endpoints
HOMGAR_BASE_URL = "https://region3.homgarus.com"
HOMGAR_LOGIN_PATH = "/auth/basic/app/login"
HOMGAR_HOMES_PATH = "/app/member/appHome/list"
HOMGAR_DEVICES_PATH = "/app/device/getDeviceByHid"

# Timer model supported
TIMER_MODEL = "HTV0540FRF"

# Default duration in seconds (10 minutes)
DEFAULT_DURATION_SECONDS = 600

# configuration.yaml keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_AREA_CODE = "area_code"

# D01 payload byte offsets (after stripping "11#" prefix and hex-decoding)
# byte[2]:    observed sequence byte toggled when opening/closing a zone
# byte[6]:    0x20 | zone_addr = zone running, 0x00 = all off
# byte[24:28]: LE uint32 = stop unix timestamp (seconds)
# byte[42:44]: LE uint16 = duration in seconds
PAYLOAD_SEQUENCE_BYTE_OFFSET = 2
ZONE_RUNNING_FLAG = 0x20
ZONE_FLAG_BYTE_OFFSET = 6
STOP_TS_BYTE_OFFSET = 24
DURATION_BYTE_OFFSET = 42
