# YgnTBPro Streamlit Dashboard

Streamlit Community Cloud version of the YgnTBPro dashboard.

## Files

- `app.py` — Streamlit user interface, filters, KPIs and chart layout.
- `functions.py` — reusable Supabase, transformation and Plotly functions.
- `requirements.txt` — Python dependencies.
- `secrets_template.toml` — template for Streamlit Secrets; do not commit real credentials.

## Streamlit Community Cloud

1. Push `app.py`, `functions.py`, and `requirements.txt` to the same GitHub repository/folder.
2. Create a Streamlit Community Cloud app and select `app.py` as the main file.
3. Open **App settings → Secrets** and add:

```toml
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_KEY = "YOUR-SUPABASE-PUBLISHABLE-KEY"
```

4. Save and reboot the app.

The dashboard caches Supabase table retrieval and preprocessing for 15 minutes. The sidebar filters are applied in memory, and only the selected dashboard section is rendered.
