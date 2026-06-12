import streamlit as st
import requests
from datetime import datetime, timedelta
import pandas as pd
from collections import defaultdict

st.set_page_config(page_title="Bsale - Recepciones y Costos", page_icon="📦", layout="wide")

# ==================== CONFIGURACIÓN ====================
ACCESS_TOKEN = "027fa2348b50d5ecd2d2a469f07c464e85cf176d"
BASE_URL = "https://api.bsale.io/v1"
HEADERS = {"access_token": ACCESS_TOKEN, "Content-Type": "application/json"}

# ==================== CSS ====================
st.markdown("""
<style>
.main-header { font-size: 2rem; font-weight: 700; color: #1a1a2e; margin-bottom: 0.5rem; }
.subheader { font-size: 1.1rem; color: #555; margin-bottom: 1.5rem; }
.metric-card { background: #f8f9fa; border-radius: 12px; padding: 1.2rem; border: 1px solid #e9ecef; text-align: center; }
.metric-value { font-size: 1.8rem; font-weight: 700; color: #1a1a2e; }
.metric-label { font-size: 0.85rem; color: #888; margin-top: 0.3rem; }
.dataframe-container { border-radius: 12px; overflow: hidden; border: 1px solid #e9ecef; }
.stTabs [data-baseweb="tab-list"] { gap: 8px; }
.stTabs [data-baseweb="tab"] { padding: 0.8rem 1.5rem; border-radius: 8px 8px 0 0; }
.section-title { font-size: 1.3rem; font-weight: 600; color: #1a1a2e; margin: 1.5rem 0 0.8rem 0; padding-bottom: 0.5rem; border-bottom: 2px solid #e9ecef; }
.filter-label { font-weight: 600; color: #444; margin-bottom: 0.3rem; }
</style>
""", unsafe_allow_html=True)

# ==================== FUNCIONES API ====================
@st.cache_data(ttl=300)
def get_receptions(start_date, end_date):
    """Obtiene recepciones de stock con detalles"""
    receptions = []
    offset = 0
    start_ts = int(start_date.timestamp())
    end_ts = int(end_date.timestamp())
    
    while True:
        url = f"{BASE_URL}/stocks/receptions.json?limit=50&offset={offset}&admissiondate=[{start_ts},{end_ts}]"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get('items', [])
            if not items:
                break
            
            for item in items:
                reception_id = item.get('id')
                # Obtener detalles
                details_url = f"{BASE_URL}/stocks/receptions/{reception_id}/details.json"
                d_resp = requests.get(details_url, headers=HEADERS, timeout=30)
                if d_resp.status_code == 200:
                    d_data = d_resp.json()
                    d_items = d_data.get('items', [])
                    for d in d_items:
                        variant_id = d.get('variant', {}).get('id', '')
                        # Obtener info de producto
                        var_url = f"{BASE_URL}/variants/{variant_id}.json"
                        v_resp = requests.get(var_url, headers=HEADERS, timeout=30)
                        product_name = 'Sin nombre'
                        variant_code = 'Sin código'
                        if v_resp.status_code == 200:
                            v_data = v_resp.json()
                            product_name = v_data.get('product', {}).get('name', 'Sin nombre')
                            variant_code = v_data.get('code', 'Sin código')
                        
                        receptions.append({
                            'ID': reception_id,
                            'Documento': item.get('document', 'Sin Documento'),
                            'Numero': item.get('documentNumber', ''),
                            'Fecha': datetime.fromtimestamp(item.get('admissionDate', 0)).strftime('%Y-%m-%d'),
                            'Nota': item.get('note', ''),
                            'Producto': product_name,
                            'Codigo': variant_code,
                            'Cantidad': d.get('quantity', 0),
                            'Costo': d.get('cost', 0),
                            'Sucursal': item.get('office', {}).get('id', ''),
                        })
            
            if len(items) < 50:
                break
            offset += 50
        except Exception as e:
            st.error(f"Error obteniendo recepciones: {e}")
            break
    
    return pd.DataFrame(receptions)

@st.cache_data(ttl=300)
def get_documents_with_costs(start_date, end_date):
    """Obtiene documentos con costos detallados"""
    results = []
    offset = 0
    start_ts = int(start_date.timestamp())
    end_ts = int(end_date.timestamp())
    
    while True:
        url = f"{BASE_URL}/documents.json?limit=50&offset={offset}&emissiondate=[{start_ts},{end_ts}]"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get('items', [])
            if not items:
                break
            
            for doc in items:
                doc_id = doc.get('id')
                # Obtener costos del documento
                cost_url = f"{BASE_URL}/documents/costs.json?documentid={doc_id}"
                c_resp = requests.get(cost_url, headers=HEADERS, timeout=30)
                if c_resp.status_code == 200:
                    c_data = c_resp.json()
                    total_cost = c_data.get('totalCost', 0)
                    if total_cost > 0:
                        cost_details = c_data.get('cost_detail', [])
                        for cd in cost_details:
                            variant = cd.get('variant', {})
                            shipping = cd.get('shipping_detail', {})
                            results.append({
                                'Documento': doc.get('name', 'N/A'),
                                'Numero': doc.get('number', ''),
                                'ID': doc_id,
                                'Fecha': datetime.fromtimestamp(doc.get('emissionDate', 0)).strftime('%Y-%m-%d'),
                                'Producto': variant.get('description', 'Sin descripción'),
                                'Codigo': variant.get('code', 'Sin código'),
                                'Cantidad': shipping.get('quantity', 0),
                                'Costo Unitario': shipping.get('variantCost', 0),
                                'Costo Total': shipping.get('variantTotalCost', 0),
                            })
            
            if len(items) < 50:
                break
            offset += 50
        except Exception as e:
            st.error(f"Error obteniendo documentos: {e}")
            break
    
    return pd.DataFrame(results)

# ==================== UI ====================
st.markdown('<div class="main-header">📦 Bsale — Recepciones y Costos</div>', unsafe_allow_html=True)
st.markdown('<div class="subheader">Visualiza recepciones de stock y costos de productos por fecha</div>', unsafe_allow_html=True)

# Filtros
st.markdown('<div class="section-title">🔍 Filtros</div>', unsafe_allow_html=True)
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown('<div class="filter-label">Fecha desde</div>', unsafe_allow_html=True)
    start_date = st.date_input("", value=datetime(2023, 1, 1), label_visibility="collapsed")
with c2:
    st.markdown('<div class="filter-label">Fecha hasta</div>', unsafe_allow_html=True)
    end_date = st.date_input("", value=datetime.now(), label_visibility="collapsed")
with c3:
    st.markdown('<div class="filter-label">Tipo</div>', unsafe_allow_html=True)
    tipo_vista = st.selectbox("", ["Recepciones", "Costos de Documentos"], label_visibility="collapsed")

# Convertir a datetime
start_dt = datetime.combine(start_date, datetime.min.time())
end_dt = datetime.combine(end_date, datetime.max.time())

# Botón de búsqueda
if st.button("🔎 Buscar datos", type="primary", use_container_width=True):
    with st.spinner("Conectando con Bsale..."):
        if tipo_vista == "Recepciones":
            df = get_receptions(start_dt, end_dt)
            
            if df.empty:
                st.info("No se encontraron recepciones en el rango de fechas seleccionado.")
            else:
                # Métricas
                total_items = len(df)
                total_cantidad = df['Cantidad'].sum()
                total_costo = df['Costo'].sum()
                unique_products = df['Codigo'].nunique()
                
                st.markdown('<div class="section-title">📊 Resumen</div>', unsafe_allow_html=True)
                m1, m2, m3, m4 = st.columns(4)
                m1.markdown(f'<div class="metric-card"><div class="metric-value">{total_items}</div><div class="metric-label">Items Recepcionados</div></div>', unsafe_allow_html=True)
                m2.markdown(f'<div class="metric-card"><div class="metric-value">{total_cantidad:,.0f}</div><div class="metric-label">Total Unidades</div></div>', unsafe_allow_html=True)
                m3.markdown(f'<div class="metric-card"><div class="metric-value">${total_costo:,.2f}</div><div class="metric-label">Costo Total</div></div>', unsafe_allow_html=True)
                m4.markdown(f'<div class="metric-card"><div class="metric-value">{unique_products}</div><div class="metric-label">Productos Únicos</div></div>', unsafe_allow_html=True)
                
                st.markdown('<div class="section-title">📋 Detalle de Recepciones</div>', unsafe_allow_html=True)
                
                # Formatear dataframe para mostrar
                df_display = df.copy()
                df_display['Costo'] = df_display['Costo'].apply(lambda x: f"${x:,.2f}" if x > 0 else "—")
                
                st.dataframe(
                    df_display[['Fecha', 'Documento', 'Numero', 'Producto', 'Codigo', 'Cantidad', 'Costo', 'Nota']],
                    use_container_width=True,
                    height=500,
                    column_config={
                        'Fecha': st.column_config.DateColumn('Fecha', format='YYYY-MM-DD'),
                        'Cantidad': st.column_config.NumberColumn('Cantidad', format='%.0f'),
                    }
                )
                
                # Descargar CSV
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    "⬇️ Descargar CSV",
                    csv,
                    f"recepciones_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv",
                    "text/csv",
                    use_container_width=True
                )
                
                # Gráfico de productos más recepcionados
                st.markdown('<div class="section-title">📈 Top Productos por Cantidad</div>', unsafe_allow_html=True)
                top_products = df.groupby('Codigo')['Cantidad'].sum().sort_values(ascending=False).head(15)
                st.bar_chart(top_products)
                
        else:  # Costos de Documentos
            df = get_documents_with_costs(start_dt, end_dt)
            
            if df.empty:
                st.info("No se encontraron documentos con costos en el rango de fechas seleccionado.")
            else:
                # Métricas
                total_items = len(df)
                total_cantidad = df['Cantidad'].sum()
                total_costo = df['Costo Total'].sum()
                unique_products = df['Codigo'].nunique()
                
                st.markdown('<div class="section-title">📊 Resumen</div>', unsafe_allow_html=True)
                m1, m2, m3, m4 = st.columns(4)
                m1.markdown(f'<div class="metric-card"><div class="metric-value">{total_items}</div><div class="metric-label">Items con Costo</div></div>', unsafe_allow_html=True)
                m2.markdown(f'<div class="metric-card"><div class="metric-value">{total_cantidad:,.0f}</div><div class="metric-label">Total Unidades</div></div>', unsafe_allow_html=True)
                m3.markdown(f'<div class="metric-card"><div class="metric-value">${total_costo:,.2f}</div><div class="metric-label">Costo Total</div></div>', unsafe_allow_html=True)
                m4.markdown(f'<div class="metric-card"><div class="metric-value">{unique_products}</div><div class="metric-label">Productos Únicos</div></div>', unsafe_allow_html=True)
                
                st.markdown('<div class="section-title">📋 Detalle de Costos</div>', unsafe_allow_html=True)
                
                # Formatear dataframe
                df_display = df.copy()
                df_display['Costo Unitario'] = df_display['Costo Unitario'].apply(lambda x: f"${x:,.2f}")
                df_display['Costo Total'] = df_display['Costo Total'].apply(lambda x: f"${x:,.2f}")
                
                st.dataframe(
                    df_display[['Fecha', 'Documento', 'Numero', 'Producto', 'Codigo', 'Cantidad', 'Costo Unitario', 'Costo Total']],
                    use_container_width=True,
                    height=500,
                    column_config={
                        'Fecha': st.column_config.DateColumn('Fecha', format='YYYY-MM-DD'),
                        'Cantidad': st.column_config.NumberColumn('Cantidad', format='%.0f'),
                    }
                )
                
                # Descargar CSV
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    "⬇️ Descargar CSV",
                    csv,
                    f"costos_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv",
                    "text/csv",
                    use_container_width=True
                )
                
                # Gráfico de costos por producto
                st.markdown('<div class="section-title">📈 Top Productos por Costo Total</div>', unsafe_allow_html=True)
                top_costs = df.groupby('Codigo')['Costo Total'].sum().sort_values(ascending=False).head(15)
                st.bar_chart(top_costs)
else:
    st.info("👆 Selecciona el rango de fechas y presiona 'Buscar datos' para cargar la información.")

# Footer
st.markdown("""
<div style="text-align: center; color: #888; margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e9ecef;">
    Conectado a Bsale API | Datos actualizados cada 5 minutos
</div>
""", unsafe_allow_html=True)
