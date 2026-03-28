from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

st.title("Alerts")
st.info(
    "Base preparada. La integración real de alertas, incluyendo Telegram, "
    "queda para una siguiente iteración."
)
