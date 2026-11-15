"""挂载到 /docs 的演示请求：重复条码、交叉候选、等价最优。"""

DEMO_EXAMPLES = {
    "duplicate_barcodes": {
        "summary": "重复条码：同条码多条记录，按时间窗区分",
        "description": (
            "条码 BC-100 在两侧各出现两次，仅靠条码无法确定配对；"
            "10 分钟时间窗把配对唯一锁定，三对全部成为确定配对。"
        ),
        "value": {
            "sampling_records": [
                {"id": "S-01", "barcode": "BC-100", "timestamp": "2026-11-14T08:00:00Z"},
                {"id": "S-02", "barcode": "BC-100", "timestamp": "2026-11-14T08:30:00Z"},
                {"id": "S-03", "barcode": "BC-200", "timestamp": "2026-11-14T09:00:00Z"},
            ],
            "detection_records": [
                {"id": "D-01", "barcode": "BC-100", "timestamp": "2026-11-14T08:04:00Z"},
                {"id": "D-02", "barcode": "BC-100", "timestamp": "2026-11-14T08:26:00Z"},
                {"id": "D-03", "barcode": "BC-200", "timestamp": "2026-11-14T09:06:00Z"},
            ],
        },
    },
    "crossing_candidates": {
        "summary": "交叉候选：时间窗交叉，贪心最近邻反而更差",
        "description": (
            "S-12 与 D-11 仅差 60 秒，若贪心占用，S-11 只能配 D-12（480 秒），"
            "总差 540 秒；全局最优为 S-11↔D-11 + S-12↔D-12，共 420 秒，两对均为确定配对。"
            "D-13 超出所有时间窗，始终未配对。"
        ),
        "value": {
            "sampling_records": [
                {"id": "S-11", "barcode": "BC-300", "timestamp": "2026-11-14T10:00:00Z"},
                {"id": "S-12", "barcode": "BC-300", "timestamp": "2026-11-14T10:04:00Z"},
            ],
            "detection_records": [
                {"id": "D-11", "barcode": "BC-300", "timestamp": "2026-11-14T10:03:00Z"},
                {"id": "D-12", "barcode": "BC-300", "timestamp": "2026-11-14T10:08:00Z"},
                {"id": "D-13", "barcode": "BC-300", "timestamp": "2026-11-14T10:30:00Z"},
            ],
        },
    },
    "equivalent_optima": {
        "summary": "等价最优：多套同等最优方案，只输出确定配对",
        "description": (
            "BC-400 下 S-21/S-22 与 D-21/D-22 形成两套成本相同（各 600 秒）的最优方案，"
            "四条记录全部待核查（始终被配对但对象不定），接口不替质控员任选一套；"
            "BC-500 是唯一确定配对；S-24、D-24 无任何候选，始终未配对。"
        ),
        "value": {
            "sampling_records": [
                {"id": "S-21", "barcode": "BC-400", "timestamp": "2026-11-14T12:00:00Z"},
                {"id": "S-22", "barcode": "BC-400", "timestamp": "2026-11-14T12:10:00Z"},
                {"id": "S-23", "barcode": "BC-500", "timestamp": "2026-11-14T13:00:00Z"},
                {"id": "S-24", "barcode": "BC-600", "timestamp": "2026-11-14T15:00:00Z"},
            ],
            "detection_records": [
                {"id": "D-21", "barcode": "BC-400", "timestamp": "2026-11-14T12:05:00Z"},
                {"id": "D-22", "barcode": "BC-400", "timestamp": "2026-11-14T12:05:00Z"},
                {"id": "D-23", "barcode": "BC-500", "timestamp": "2026-11-14T13:03:00Z"},
                {"id": "D-24", "barcode": "BC-700", "timestamp": "2026-11-14T15:00:00Z"},
            ],
        },
    },
}
