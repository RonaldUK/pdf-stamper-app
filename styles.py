import os
import base64
import streamlit as st

RUTA_LOGO = "logo.png"

def obtener_base64_logo():
    rutas_posibles = [
        RUTA_LOGO,
        os.path.join(os.path.dirname(__file__), RUTA_LOGO)
    ]
    for ruta in rutas_posibles:
        if os.path.exists(ruta):
            with open(ruta, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode()
    return None

def aplicar_estilos_custom():
    st.markdown("""
        <style>
        .stApp {
            background: radial-gradient(ellipse at bottom, #111827 0%, #030712 100%);
            color: #f3f4f6;
        }

        #MainMenu, footer {visibility: hidden;}

        .logo-img {
            max-width: 260px;
            height: auto;
            margin-bottom: 10px;
            filter: drop-shadow(0 0 20px rgba(56, 189, 248, 0.35));
            transition: transform 0.4s ease;
        }

        .logo-img:hover {
            transform: scale(1.03);
            filter: drop-shadow(0 0 30px rgba(56, 189, 248, 0.6));
        }

        .sub-title {
            color: #94a3b8;
            font-size: 0.85rem;
            letter-spacing: 2px;
            text-transform: uppercase;
            font-weight: 500;
            margin-bottom: 25px;
        }

        div[data-baseweb="input"] {
            background-color: rgba(15, 23, 42, 0.8) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            border-radius: 10px !important;
            color: #ffffff !important;
        }

        div[data-baseweb="input"]:focus-within {
            border-color: #38bdf8 !important;
            box-shadow: 0 0 10px rgba(56, 189, 248, 0.3) !important;
        }

        .stButton>button {
            background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 10px !important;
            padding: 12px 24px !important;
            font-weight: 600 !important;
            letter-spacing: 1px !important;
            box-shadow: 0 4px 15px rgba(2, 132, 199, 0.4) !important;
            transition: all 0.3s ease !important;
        }

        .stButton>button:hover {
            box-shadow: 0 6px 25px rgba(56, 189, 248, 0.7) !important;
            transform: translateY(-2px);
        }
        </style>
    """, unsafe_allow_html=True)