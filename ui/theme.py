# Targets Streamlit private internals. Verified on Streamlit 1.63.0. Re-check all [data-testid]/[role] selectors on upgrade.

import streamlit as st

SELLOMIZE_THEME_CSS = """<style>
    /* Global Canvas & Ambient Lighting (60% Crisp White Canvas) */
    .stApp {
        background: radial-gradient(1200px 600px at 50% -120px, rgba(8, 55, 49, 0.04) 0%, transparent 70%),
                    radial-gradient(900px 500px at 100% 5%, rgba(253, 77, 27, 0.03) 0%, transparent 60%),
                    #FFFFFF !important;
        background-attachment: fixed !important;
        color: #1E293B !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
    }

    /* Custom Sleek Scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    ::-webkit-scrollbar-track {
        background: #F8FAF9;
    }
    ::-webkit-scrollbar-thumb {
        background: rgba(8, 55, 49, 0.25);
        border-radius: 8px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: #083731;
    }

    /* Global Typography (30% Sellomize Signature Deep Pine Green #083731) */
    h1, h2, h3, h4, h5, h6 {
        color: #083731 !important;
        letter-spacing: -0.4px !important;
        font-weight: 700 !important;
    }
    p, label {
        color: #334155;
    }
    hr {
        border: none !important;
        height: 1px !important;
        background: linear-gradient(90deg, transparent, rgba(8, 55, 49, 0.15), transparent) !important;
        margin: 1.5rem 0 !important;
    }

    /* Branded Floating Header (30% Sellomize Deep Pine Green #083731 + 10% Orange Accent) */
    .sellomize-header-container {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: linear-gradient(135deg, #083731 0%, #0d4a42 100%);
        border: 1px solid rgba(8, 55, 49, 0.3);
        border-radius: 16px;
        padding: 1.15rem 1.8rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 10px 25px -5px rgba(8, 55, 49, 0.2), 0 4px 10px rgba(0, 0, 0, 0.04);
        position: relative;
        overflow: hidden;
    }
    .sellomize-header-container::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 3px;
        background: linear-gradient(90deg, transparent, #FD4D1B 40%, #10B981 80%, transparent);
    }
    .sellomize-header-left {
        display: flex;
        align-items: center;
        gap: 1.25rem;
    }
    .sellomize-logo-img {
        height: 56px;
        width: 56px;
        object-fit: cover;
        border-radius: 12px;
        border: 2px solid #FD4D1B;
        box-shadow: 0 4px 16px rgba(253, 77, 27, 0.35);
    }
    .sellomize-logo-fallback {
        font-size: 2.2rem;
        background: #083731;
        border-radius: 12px;
        padding: 6px 12px;
        border: 2px solid #FD4D1B;
        box-shadow: 0 4px 16px rgba(253, 77, 27, 0.35);
    }
    .sellomize-brand-title {
        font-size: 1.95rem;
        font-weight: 900;
        letter-spacing: 1.4px;
        color: #FFFFFF;
        line-height: 1.1;
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .sellomize-highlight {
        color: #FD4D1B;
        text-shadow: 0 0 16px rgba(253, 77, 27, 0.4);
    }
    .sellomize-brand-subtitle {
        font-size: 0.88rem;
        font-weight: 500;
        color: #CCFBF1;
        margin-top: 4px;
        letter-spacing: 0.4px;
    }
    .sellomize-header-right {
        display: flex;
        align-items: center;
        gap: 0.85rem;
    }
    .system-status-pill {
        display: flex;
        align-items: center;
        gap: 8px;
        background: rgba(255, 255, 255, 0.12);
        border: 1px solid rgba(255, 255, 255, 0.25);
        color: #FFFFFF;
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.8px;
        text-transform: uppercase;
    }
    .status-indicator-dot {
        width: 8px;
        height: 8px;
        background-color: #34D399;
        border-radius: 50%;
        box-shadow: 0 0 10px #34D399;
        animation: pulse-dot 2s infinite ease-in-out;
    }
    @keyframes pulse-dot {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.35; transform: scale(0.8); }
    }
    .sellomize-header-badge {
        background: #083731;
        color: #CCFBF1;
        border: 1px solid rgba(255, 255, 255, 0.25);
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.8px;
        text-transform: uppercase;
        box-shadow: 0 2px 8px rgba(8, 55, 49, 0.25);
    }

    /* Executive Stat Cards (60% Crisp White Canvas with 30% Green / 10% Orange Accents) */
    .stats-grid {
        display: grid;
        grid-template-columns: repeat(7, 1fr);
        gap: 0.9rem;
        margin-bottom: 1.5rem;
    }
    @media (max-width: 1280px) {
        .stats-grid {
            grid-template-columns: repeat(4, 1fr);
        }
    }
    @media (max-width: 768px) {
        .stats-grid {
            grid-template-columns: repeat(2, 1fr);
        }
    }
    .stat-card {
        background: #FFFFFF;
        border: 1px solid rgba(8, 55, 49, 0.14);
        border-radius: 14px;
        padding: 1.15rem 1.35rem;
        box-shadow: 0 2px 12px rgba(8, 55, 49, 0.04), 0 1px 2px rgba(0, 0, 0, 0.03);
        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
    }
    .stat-card:hover {
        transform: translateY(-2px);
        border-color: #083731;
        box-shadow: 0 8px 24px rgba(8, 55, 49, 0.1);
    }
    .stat-card-highlight {
        border-color: rgba(253, 77, 27, 0.4);
        background: linear-gradient(145deg, #FFFFFF 0%, rgba(253, 77, 27, 0.04) 100%);
    }
    .stat-card-highlight:hover {
        border-color: #FD4D1B;
        box-shadow: 0 8px 24px rgba(253, 77, 27, 0.15);
    }
    .stat-card-alert {
        border-color: rgba(239, 68, 68, 0.35);
        background: linear-gradient(145deg, #FFFFFF 0%, rgba(239, 68, 68, 0.04) 100%);
    }
    .stat-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.5rem;
    }
    .stat-label {
        font-size: 0.78rem;
        font-weight: 700;
        color: #475569;
        text-transform: uppercase;
        letter-spacing: 0.75px;
    }
    .stat-icon-badge {
        font-size: 1.1rem;
        background: #F0F5F4;
        padding: 3px 8px;
        border-radius: 8px;
        border: 1px solid rgba(8, 55, 49, 0.12);
    }
    .stat-value {
        font-size: 2.1rem;
        font-weight: 800;
        color: #083731;
        line-height: 1.1;
        letter-spacing: -0.5px;
        margin-bottom: 0.3rem;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .stat-val-glow {
        color: #083731;
    }
    .stat-val-alert {
        color: #EF4444;
    }
    .stat-sub {
        font-size: 0.78rem;
        color: #64748B;
        font-weight: 500;
    }
    .stat-sub-alert {
        color: #EF4444;
        font-weight: 600;
    }
    .stat-sub-clean {
        color: #059669;
        font-weight: 600;
    }

    /* Modern Segmented Navigation Tabs (Sellomize 60-30-10 Enterprise Design) */
    .stTabs,
    div[data-testid="stTabs"] {
        margin-bottom: 1.6rem !important;
    }

    /* Tab List Container Bar (Swipeable on touch screens) */
    .stTabs [role="tablist"],
    div[data-testid="stTabs"] [role="tablist"],
    div[data-testid="stTabs"] > div:first-child,
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px !important;
        background: #F0F5F4 !important;
        padding: 6px !important;
        border-radius: 12px !important;
        border: 1px solid rgba(8, 55, 49, 0.15) !important;
        box-shadow: inset 0 1px 3px rgba(8, 55, 49, 0.05) !important;
        display: flex !important;
        align-items: center !important;
        position: relative !important;
        border-bottom: 1px solid rgba(8, 55, 49, 0.15) !important;
        overflow-x: auto !important;
        max-width: 100% !important;
        white-space: nowrap !important;
        flex-wrap: nowrap !important;
        scrollbar-width: none !important;
        -webkit-overflow-scrolling: touch !important;
    }
    .stTabs [role="tablist"]::-webkit-scrollbar {
        display: none !important;
    }

    /* Eliminate the default bottom pseudo-line on tablist */
    .stTabs [role="tablist"]::after,
    div[data-testid="stTabs"] [role="tablist"]::after {
        display: none !important;
        content: none !important;
        height: 0 !important;
        background: transparent !important;
        border: none !important;
    }

    /* Base Tab Pill (Unselected State) */
    .stTabs [data-testid="stTab"],
    .stTabs [role="tab"],
    .stTabs [data-baseweb="tab"],
    div[data-testid="stTabs"] button[role="tab"] {
        height: 42px !important;
        border-radius: 8px !important;
        padding: 0 1.25rem !important;
        border: 1px solid transparent !important;
        background: transparent !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
        cursor: pointer !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        text-decoration: none !important;
        box-shadow: none !important;
        flex-shrink: 0 !important;
        white-space: nowrap !important;
    }

    /* Unselected Tab Label & Icons (Sellomize Signature Deep Pine Green) */
    .stTabs [data-testid="stTab"] p,
    .stTabs [role="tab"] p,
    .stTabs [data-baseweb="tab"] p,
    div[data-testid="stTabs"] button[role="tab"] p,
    .stTabs [data-testid="stTab"] span,
    .stTabs [role="tab"] span,
    .stTabs [data-testid="stTab"] div,
    div[data-testid="stTabs"] button[role="tab"] div {
        color: #083731 !important;
        font-weight: 600 !important;
        font-size: 0.92rem !important;
        margin: 0 !important;
        padding: 0 !important;
        letter-spacing: 0.2px !important;
        transition: color 0.15s ease !important;
    }

    /* Unselected Tab Hover State */
    .stTabs [data-testid="stTab"]:hover,
    .stTabs [role="tab"]:hover,
    .stTabs [data-baseweb="tab"]:hover,
    div[data-testid="stTabs"] button[role="tab"]:hover {
        background: rgba(8, 55, 49, 0.08) !important;
        border-color: rgba(8, 55, 49, 0.16) !important;
    }
    .stTabs [data-testid="stTab"]:hover p,
    .stTabs [role="tab"]:hover p,
    div[data-testid="stTabs"] button[role="tab"]:hover p {
        color: #083731 !important;
    }

    /* Active Selected Tab (30% Deep Pine Green #083731 Container) */
    .stTabs [data-testid="stTab"][aria-selected="true"],
    .stTabs [data-testid="stTab"][data-selected="true"],
    .stTabs [role="tab"][aria-selected="true"],
    .stTabs [role="tab"][data-selected="true"],
    .stTabs [aria-selected="true"],
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"],
    div[data-testid="stTabs"] button[role="tab"][data-selected="true"] {
        background: #083731 !important;
        border: 1px solid #083731 !important;
        box-shadow: 0 4px 14px rgba(8, 55, 49, 0.28) !important;
        border-radius: 8px !important;
    }

    /* Active Selected Tab Text (CRISP 100% PURE WHITE #FFFFFF WITH ZERO LEAK) */
    .stTabs [data-testid="stTab"][aria-selected="true"] p,
    .stTabs [data-testid="stTab"][data-selected="true"] p,
    .stTabs [role="tab"][aria-selected="true"] p,
    .stTabs [role="tab"][data-selected="true"] p,
    .stTabs [aria-selected="true"] p,
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] p,
    div[data-testid="stTabs"] button[role="tab"][data-selected="true"] p,
    .stTabs [data-testid="stTab"][aria-selected="true"] span,
    .stTabs [data-testid="stTab"][data-selected="true"] span,
    .stTabs [role="tab"][aria-selected="true"] span,
    .stTabs [aria-selected="true"] span,
    .stTabs [aria-selected="true"] div,
    .stTabs [aria-selected="true"] * {
        color: #FFFFFF !important;
        font-weight: 700 !important;
        fill: #FFFFFF !important;
        text-shadow: 0 1px 2px rgba(0, 0, 0, 0.25) !important;
    }

    /* Eradicate any Selection Indicator Bar or Underline artifact */
    .stTabs .react-aria-SelectionIndicator,
    .stTabs [data-testid="stTab"] .react-aria-SelectionIndicator,
    div[data-testid="stTabs"] .react-aria-SelectionIndicator,
    .stTabs [data-baseweb="tab-highlight"],
    .stTabs [data-baseweb="tab-border"] {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        height: 0 !important;
        width: 0 !important;
        background: transparent !important;
        border: none !important;
    }

    /* Primary Action Buttons (Action Blue #2563EB - Clean, High-Converting SaaS CTAs) */
    button[kind="primary"],
    [data-testid="baseButton-primary"],
    div.stButton > button[kind="primary"],
    div.stFormSubmitButton > button[kind="primary"] {
        background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%) !important;
        color: #FFFFFF !important;
        font-weight: 700 !important;
        font-size: 0.92rem !important;
        border: 1px solid #1D4ED8 !important;
        border-radius: 8px !important;
        padding: 0.55rem 1.4rem !important;
        box-shadow: 0 4px 14px rgba(37, 99, 235, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.25) !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
        letter-spacing: 0.3px !important;
    }
    button[kind="primary"] p,
    button[kind="primary"] span,
    [data-testid="baseButton-primary"] p,
    [data-testid="baseButton-primary"] span,
    div.stButton > button[kind="primary"] p,
    div.stFormSubmitButton > button[kind="primary"] p {
        color: #FFFFFF !important;
        font-weight: 700 !important;
    }
    button[kind="primary"]:hover,
    [data-testid="baseButton-primary"]:hover,
    div.stButton > button[kind="primary"]:hover,
    div.stFormSubmitButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #3B82F6 0%, #2563EB 100%) !important;
        box-shadow: 0 6px 20px rgba(37, 99, 235, 0.4) !important;
        transform: translateY(-1px) !important;
    }
    button[kind="primary"]:active,
    [data-testid="baseButton-primary"]:active {
        transform: translateY(0px) !important;
    }

    /* Secondary Action Buttons (30% Pine Green Accent on 60% White) */
    button[kind="secondary"],
    [data-testid="baseButton-secondary"],
    .stButton > button:not([kind="primary"]),
    .stDownloadButton > button {
        background: #FFFFFF !important;
        color: #083731 !important;
        border: 1.5px solid #083731 !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 0.88rem !important;
        padding: 0.5rem 1.2rem !important;
        box-shadow: 0 2px 6px rgba(8, 55, 49, 0.05) !important;
        transition: all 0.2s ease-in-out !important;
    }
    button[kind="secondary"] p,
    button[kind="secondary"] span,
    [data-testid="baseButton-secondary"] p,
    [data-testid="baseButton-secondary"] span,
    .stButton > button:not([kind="primary"]) p,
    .stDownloadButton > button p {
        color: #083731 !important;
        font-weight: 600 !important;
    }
    button[kind="secondary"]:hover,
    [data-testid="baseButton-secondary"]:hover,
    .stButton > button:not([kind="primary"]):hover,
    .stDownloadButton > button:hover {
        background: #083731 !important;
        border-color: #083731 !important;
        color: #FFFFFF !important;
        box-shadow: 0 4px 14px rgba(8, 55, 49, 0.2) !important;
        transform: translateY(-1px) !important;
    }
    button[kind="secondary"]:hover p,
    button[kind="secondary"]:hover span,
    [data-testid="baseButton-secondary"]:hover p,
    .stButton > button:not([kind="primary"]):hover p,
    .stDownloadButton > button:hover p {
        color: #FFFFFF !important;
    }

    /* Form Containers & Expanders (60% Crisp White) */
    div[data-testid="stForm"] {
        background: #FFFFFF !important;
        border: 1px solid rgba(8, 55, 49, 0.16) !important;
        border-radius: 14px !important;
        padding: 1.5rem 1.75rem !important;
        box-shadow: 0 6px 24px rgba(8, 55, 49, 0.05) !important;
    }
    div[data-testid="stExpander"] {
        background: #FFFFFF !important;
        border: 1px solid rgba(8, 55, 49, 0.15) !important;
        border-radius: 12px !important;
        box-shadow: 0 2px 10px rgba(8, 55, 49, 0.04) !important;
        overflow: hidden !important;
        margin-bottom: 12px !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="stExpander"]:hover {
        border-color: #083731 !important;
    }
    div[data-testid="stExpander"] summary {
        font-weight: 700 !important;
        color: #083731 !important;
        font-size: 0.95rem !important;
    }

    /* Inputs, Textareas, and Dropdowns (Sellomize High-Precision SaaS Styling) */
    div[data-baseweb="input"],
    div[data-baseweb="textarea"],
    div[data-baseweb="select"] > div,
    div[data-testid="stTextInputRootElement"],
    div[data-testid="stTextAreaRootElement"],
    div[data-testid="stSelectbox"] > div,
    div[data-testid="stNumberInputContainer"] {
        background-color: #FFFFFF !important;
        border: 1.5px solid #CBD5E1 !important;
        border-radius: 8px !important;
        color: #0F172A !important;
        box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.04) !important;
        transition: all 0.2s ease !important;
    }
    div[data-baseweb="input"]:focus-within,
    div[data-baseweb="textarea"]:focus-within,
    div[data-baseweb="select"]:focus-within > div,
    div[data-testid="stTextInputRootElement"]:focus-within,
    div[data-testid="stTextAreaRootElement"]:focus-within,
    div[data-testid="stSelectbox"]:focus-within > div,
    div[data-testid="stNumberInputContainer"]:focus-within {
        border-color: #FD4D1B !important;
        box-shadow: 0 0 0 3px rgba(253, 77, 27, 0.18), inset 0 1px 2px rgba(0, 0, 0, 0.04) !important;
        background-color: #FFFFFF !important;
    }
    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea,
    input[data-testid="stTextInputField"],
    textarea[data-testid="stTextAreaField"],
    .stTextInput input,
    .stTextArea textarea {
        color: #0F172A !important;
        background-color: transparent !important;
        font-size: 0.92rem !important;
    }
    div[data-baseweb="input"] input::placeholder,
    div[data-baseweb="textarea"] textarea::placeholder,
    input[data-testid="stTextInputField"]::placeholder,
    textarea[data-testid="stTextAreaField"]::placeholder {
        color: #94A3B8 !important;
    }

    /* Progress Bars */
    div[data-testid="stProgress"] > div > div > div > div {
        background: linear-gradient(90deg, #FD4D1B, #083731) !important;
        border-radius: 10px !important;
    }
    div[data-testid="stProgress"] > div > div {
        background-color: #E2E8F0 !important;
        border-radius: 10px !important;
        border: 1px solid rgba(8, 55, 49, 0.1) !important;
    }

    /* Email Preview Box (Rich High-Contrast Email Canvas) */
    .email-preview-box {
        background: #FFFFFF !important;
        border: 1px solid rgba(8, 55, 49, 0.16) !important;
        border-left: 4px solid #FD4D1B !important;
        border-radius: 10px !important;
        padding: 1.25rem 1.5rem !important;
        margin-top: 0.6rem !important;
        margin-bottom: 0.8rem !important;
        color: #1E293B;
        box-shadow: 0 4px 16px rgba(8, 55, 49, 0.05) !important;
        line-height: 1.6 !important;
    }
    .email-preview-box table {
        border-collapse: collapse !important;
        max-width: 100% !important;
    }
    .email-preview-box img {
        max-width: 100% !important;
        height: auto !important;
    }
    .email-preview-box a {
        color: #083731 !important;
        text-decoration: underline !important;
    }

    /* Flagged Draft Card */
    .flagged-card {
        background: #FEF2F2 !important;
        border: 1.5px solid #F87171 !important;
        border-radius: 12px !important;
        padding: 1.35rem !important;
        margin-bottom: 1.2rem !important;
        color: #991B1B !important;
        box-shadow: 0 4px 16px rgba(239, 68, 68, 0.1) !important;
    }
    .flagged-banner {
        background: linear-gradient(135deg, #EF4444 0%, #DC2626 100%) !important;
        color: #FFFFFF !important;
        font-weight: 700 !important;
        padding: 6px 14px !important;
        border-radius: 6px !important;
        margin-bottom: 10px !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 6px !important;
        letter-spacing: 0.5px !important;
        box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25) !important;
    }

    /* Micro Status Badges */
    .badge-flagged {
        background: rgba(239, 68, 68, 0.12) !important;
        color: #DC2626 !important;
        border: 1px solid rgba(239, 68, 68, 0.3) !important;
        padding: 3px 10px !important;
        border-radius: 6px !important;
        font-weight: 700 !important;
        font-size: 0.8rem !important;
        letter-spacing: 0.3px !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 5px !important;
    }
    .badge-pending {
        background: rgba(245, 158, 11, 0.12) !important;
        color: #D97706 !important;
        border: 1px solid rgba(245, 158, 11, 0.3) !important;
        padding: 3px 10px !important;
        border-radius: 6px !important;
        font-weight: 700 !important;
        font-size: 0.8rem !important;
        letter-spacing: 0.3px !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 5px !important;
    }
    .badge-approved {
        background: rgba(8, 55, 49, 0.1) !important;
        color: #083731 !important;
        border: 1px solid rgba(8, 55, 49, 0.25) !important;
        padding: 3px 10px !important;
        border-radius: 6px !important;
        font-weight: 700 !important;
        font-size: 0.8rem !important;
        letter-spacing: 0.3px !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 5px !important;
    }
    .badge-sent {
        background: rgba(16, 185, 129, 0.12) !important;
        color: #059669 !important;
        border: 1px solid rgba(16, 185, 129, 0.3) !important;
        padding: 3px 10px !important;
        border-radius: 6px !important;
        font-weight: 700 !important;
        font-size: 0.8rem !important;
        letter-spacing: 0.3px !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 5px !important;
    }

    /* Syntax Guide Box */
    .syntax-help {
        background: #F0F5F4 !important;
        border: 1px solid rgba(8, 55, 49, 0.15) !important;
        border-left: 4px solid #083731 !important;
        padding: 1rem 1.25rem !important;
        border-radius: 10px !important;
        margin-bottom: 1rem !important;
        color: #1E293B !important;
        box-shadow: 0 2px 8px rgba(8, 55, 49, 0.04) !important;
    }
    .syntax-help code {
        background: #FFFFFF !important;
        color: #E93806 !important;
        padding: 2px 7px !important;
        border-radius: 5px !important;
        border: 1px solid rgba(253, 77, 27, 0.25) !important;
        font-weight: 600 !important;
    }

    /* Table & Data Editor Polish & Horizontal Scroll Indicators */
    [data-testid="stDataFrame"],
    [data-testid="stDataEditor"] {
        border: 1px solid rgba(8, 55, 49, 0.16) !important;
        border-radius: 10px !important;
        overflow-x: auto !important;
        box-shadow: 0 2px 10px rgba(8, 55, 49, 0.04) !important;
        background: #FFFFFF !important;
    }
    .crm-grid-wrapper {
        position: relative;
        border-radius: 10px;
        overflow-x: auto;
        box-shadow: inset -12px 0 12px -12px rgba(8, 55, 49, 0.18);
    }
    .crm-scroll-caption {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.8rem;
        color: #64748B;
        margin-top: 4px;
        padding: 0 2px;
    }

    /* Right-aligned low-opacity timestamp utility */
    .timestamp-right {
        display: flex !important;
        justify-content: flex-end !important;
        text-align: right !important;
        opacity: 0.45 !important;
        font-size: 0.75rem !important;
        color: #64748B !important;
        letter-spacing: 0.2px !important;
        user-select: none !important;
        margin-top: -4px !important;
        margin-bottom: 8px !important;
    }

    /* Sidebar Styling (Sellomize 30% Pine Green & 60% Crisp White) */
    section[data-testid="stSidebar"] {
        background-color: #F8FAF9 !important;
        border-right: 1px solid rgba(8, 55, 49, 0.14) !important;
        box-shadow: 2px 0 16px rgba(8, 55, 49, 0.04) !important;
    }
    section[data-testid="stSidebar"] .stMarkdown h1,
    section[data-testid="stSidebar"] .stMarkdown h2,
    section[data-testid="stSidebar"] .stMarkdown h3,
    section[data-testid="stSidebar"] .stMarkdown h4 {
        color: #083731 !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stExpander"] {
        background: #FFFFFF !important;
        border: 1px solid rgba(8, 55, 49, 0.14) !important;
        border-radius: 10px !important;
        margin-bottom: 10px !important;
    }

    /* ==========================================================================
       📱 COMPREHENSIVE MOBILE & TOUCH RESPONSIVE STYLESHEET (PHONES <= 768px)
       ========================================================================== */
    @media (max-width: 768px) {
        /* 1. Viewport & Canvas Layout: Eliminate huge default desktop padding */
        .block-container {
            padding-top: 0.75rem !important;
            padding-bottom: 2.5rem !important;
            padding-left: 0.65rem !important;
            padding-right: 0.65rem !important;
            max-width: 100% !important;
        }

        /* 2. Responsive Branded Header Bar */
        .sellomize-header-container {
            flex-direction: column !important;
            align-items: flex-start !important;
            padding: 0.95rem 1rem !important;
            gap: 0.75rem !important;
            border-radius: 12px !important;
            margin-bottom: 1rem !important;
        }
        .sellomize-header-left {
            gap: 0.75rem !important;
            width: 100% !important;
        }
        .sellomize-logo-img {
            height: 40px !important;
            width: 40px !important;
            border-radius: 10px !important;
        }
        .sellomize-logo-fallback {
            font-size: 1.5rem !important;
            padding: 4px 8px !important;
        }
        .sellomize-brand-title {
            font-size: 1.35rem !important;
            letter-spacing: 0.4px !important;
            line-height: 1.15 !important;
        }
        .sellomize-brand-subtitle {
            font-size: 0.72rem !important;
            line-height: 1.3 !important;
        }
        .sellomize-header-right {
            width: 100% !important;
            display: flex !important;
            justify-content: space-between !important;
            align-items: center !important;
            border-top: 1px solid rgba(255, 255, 255, 0.18) !important;
            padding-top: 0.55rem !important;
            gap: 0.5rem !important;
        }
        .system-status-pill,
        .sellomize-header-badge {
            padding: 3px 9px !important;
            font-size: 0.68rem !important;
        }

        /* 3. Smooth Swipeable Tab Navigation Bar on Touch Devices */
        .stTabs [role="tablist"],
        div[data-testid="stTabs"] [role="tablist"],
        div[data-testid="stTabs"] > div:first-child,
        .stTabs [data-baseweb="tab-list"] {
            padding: 4px 6px !important;
            gap: 5px !important;
            border-radius: 10px !important;
            margin-bottom: 1rem !important;
        }
        .stTabs [data-testid="stTab"],
        .stTabs [role="tab"],
        .stTabs [data-baseweb="tab"],
        div[data-testid="stTabs"] button[role="tab"] {
            height: 38px !important;
            padding: 0 0.85rem !important;
        }
        .stTabs [data-testid="stTab"] p,
        .stTabs [role="tab"] p,
        div[data-testid="stTabs"] button[role="tab"] p {
            font-size: 0.82rem !important;
        }

        /* 4. Column Stacking on Mobile Screens */
        [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
            gap: 0.65rem !important;
        }
        [data-testid="stHorizontalBlock"] > [data-testid="column"] {
            width: 100% !important;
            min-width: 100% !important;
            flex: 1 1 100% !important;
            margin-bottom: 0.2rem !important;
        }

        /* Checkbox rows (e.g. triage draft items & CRM card selection) stay neatly inline with their card */
        [data-testid="stHorizontalBlock"]:has(> [data-testid="column"] [data-testid="stCheckbox"]) {
            flex-wrap: nowrap !important;
            align-items: flex-start !important;
        }
        [data-testid="stHorizontalBlock"]:has(> [data-testid="column"] [data-testid="stCheckbox"]) > [data-testid="column"]:first-child {
            width: 36px !important;
            min-width: 36px !important;
            max-width: 42px !important;
            flex: 0 0 36px !important;
        }
        [data-testid="stHorizontalBlock"]:has(> [data-testid="column"] [data-testid="stCheckbox"]) > [data-testid="column"]:not(:first-child) {
            width: calc(100% - 42px) !important;
            min-width: 0 !important;
            flex: 1 1 auto !important;
        }

        /* 5. Mobile Touch Targets & iOS Anti-Zoom Fix */
        button,
        [data-testid="baseButton-primary"],
        [data-testid="baseButton-secondary"],
        .stButton > button,
        .stDownloadButton > button {
            min-height: 44px !important;
            font-size: 0.92rem !important;
            touch-action: manipulation !important;
            border-radius: 8px !important;
        }
        div[data-baseweb="input"] input,
        div[data-baseweb="textarea"] textarea,
        input[data-testid="stTextInputField"],
        textarea[data-testid="stTextAreaField"],
        .stTextInput input,
        .stTextArea textarea,
        div[data-baseweb="select"] {
            font-size: 16px !important; /* Prevents iOS Safari from auto-zooming on focus */
        }

        /* 6. Form Containers on Mobile */
        div[data-testid="stForm"] {
            padding: 0.95rem 1rem !important;
            border-radius: 12px !important;
        }
        div[data-testid="stExpander"] {
            border-radius: 10px !important;
            margin-bottom: 8px !important;
        }
        div[data-testid="stExpander"] summary {
            font-size: 0.88rem !important;
            padding: 0.35rem 0.1rem !important;
        }

        /* 7. Stats Grid on Mobile Phones */
        .stats-grid {
            grid-template-columns: repeat(2, 1fr) !important;
            gap: 0.65rem !important;
            margin-bottom: 1.1rem !important;
        }
        .stat-card {
            padding: 0.85rem 0.95rem !important;
            border-radius: 10px !important;
        }
        .stat-value {
            font-size: 1.55rem !important;
            margin-bottom: 0.15rem !important;
        }
        .stat-label {
            font-size: 0.68rem !important;
            letter-spacing: 0.5px !important;
        }
        .stat-sub {
            font-size: 0.72rem !important;
        }

        /* 8. Full-width Responsive Tables & Previews */
        [data-testid="stDataFrame"],
        [data-testid="stDataEditor"] {
            width: 100% !important;
            overflow-x: auto !important;
            -webkit-overflow-scrolling: touch !important;
        }
        .email-preview-box {
            padding: 0.85rem !important;
            border-radius: 8px !important;
            overflow-x: auto !important;
            word-break: break-word !important;
        }

        /* 9. Sidebar on Mobile Devices */
        section[data-testid="stSidebar"] {
            width: 86vw !important;
            max-width: 320px !important;
        }

        /* 10. Modals & Dialogs on Mobile */
        div[data-testid="stModal"],
        div[role="dialog"] {
            width: 95vw !important;
            max-width: 95vw !important;
            margin: 10px auto !important;
            padding: 1rem !important;
            border-radius: 14px !important;
        }
    }

    @media (max-width: 480px) {
        /* Single column stats on very narrow phones */
        .stats-grid {
            grid-template-columns: 1fr !important;
        }
        .sellomize-brand-title {
            font-size: 1.22rem !important;
        }
        .stat-header {
            margin-bottom: 0.35rem !important;
        }
        .stat-icon-badge {
            font-size: 0.95rem !important;
            padding: 2px 6px !important;
        }
    }

</style>"""

def apply_theme():
    """Inject the custom Sellomize SaaS stylesheet into the Streamlit app with branded tab bar logo."""
    st.markdown(SELLOMIZE_THEME_CSS, unsafe_allow_html=True)
    try:
        from ui.components import get_logo_base64
        logo_b64 = get_logo_base64()
        if logo_b64:
            tab_logo_css = f"""<style>
            .stTabs [role="tablist"]::before,
            div[data-testid="stTabs"] [role="tablist"]::before,
            div[data-testid="stTabs"] > div:first-child::before,
            .stTabs [data-baseweb="tab-list"]::before {{
                content: '' !important;
                display: inline-block !important;
                width: 28px !important;
                height: 28px !important;
                min-width: 28px !important;
                background-image: url('data:image/jpeg;base64,{logo_b64}') !important;
                background-size: cover !important;
                background-position: center !important;
                border-radius: 7px !important;
                border: 1.5px solid #FD4D1B !important;
                box-shadow: 0 2px 8px rgba(253, 77, 27, 0.35) !important;
                flex-shrink: 0 !important;
                margin-right: 8px !important;
                margin-left: 2px !important;
            }}
            </style>"""
            st.markdown(tab_logo_css, unsafe_allow_html=True)
    except Exception:
        pass
