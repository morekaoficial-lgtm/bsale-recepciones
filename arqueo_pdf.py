import streamlit as st
import requests
import re
import pandas as pd
from datetime import datetime
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="Bsale - Arqueo de Precios (PDF)", page_icon="📊", layout="wide")

# ==================== CONFIGURACIÓN ====================
ACCESS_TOKEN = "027fa2348b50d5ecd2d2a469f07c464e85cf176d"
BASE_URL = "https://api.bsale.io/v1"
HEADERS = {"access_token": ACCESS_TOKEN, "Content-Type": "application/json"}

# ==================== CSS ====================
st.markdown("""
<style>
.main-header { font-size: 2.2rem; font-weight: 700; color: #1a1a2e; margin-bottom: 0.3rem; }
.subheader { font-size: 1rem; color: #666; margin-bottom: 1rem; }
.metric-card { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 12px; padding: 1rem; color: white; text-align: center; }
.metric-value { font-size: 2rem; font-weight: 700; }
.metric-label { font-size: 0.8rem; opacity: 0.9; margin-top: 0.2rem; }
.metric-card-red { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
.metric-card-green { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); }
.metric-card-gray { background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%); color: #333; }
.metric-card-orange { background: linear-gradient(135deg, #fa709a 0%, #fee140 100%); color: #333; }
.section-title { font-size: 1.2rem; font-weight: 600; color: #1a1a2e; margin: 1rem 0 0.5rem 0; padding-bottom: 0.3rem; border-bottom: 2px solid #e9ecef; }
.stProgress > div > div { background-color: #667eea; }
.highlight-box { background: #fff3cd; border-left: 4px solid #ffc107; padding: 1rem; border-radius: 8px; margin: 0.5rem 0; }
.not-found-box { background: #f8d7da; border-left: 4px solid #dc3545; padding: 1rem; border-radius: 8px; margin: 0.5rem 0; }
</style>
""", unsafe_allow_html=True)

# ==================== CACHE DE API ====================
@st.cache_data(ttl=300)
def fetch_all_products():
    """Obtiene todos los productos de Bsale con cache"""
    products = []
    offset = 0
    limit = 50
    
    while True:
        url = f"{BASE_URL}/products.json?limit={limit}&offset={offset}&expand=variants"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            break
        data = resp.json()
        items = data.get('items', [])
        if not items:
            break
        
        for item in items:
            prod_id = item.get('id')
            prod_name = item.get('name', '')
            
            var_url = f"{BASE_URL}/products/{prod_id}/variants.json"
            v_resp = requests.get(var_url, headers=HEADERS, timeout=30)
            if v_resp.status_code == 200:
                v_data = v_resp.json()
                variants = v_data.get('items', [])
                for v in variants:
                    products.append({
                        'product_id': prod_id,
                        'product_name': prod_name,
                        'variant_id': v.get('id'),
                        'variant_desc': v.get('description', ''),
                        'variant_code': v.get('code', ''),
                        'stock': v.get('stock', 0),
                        'costo_promedio': v.get('averageCost', 0)
                    })
        
        if len(items) < limit:
            break
        offset += limit
    
    return pd.DataFrame(products)

@st.cache_data(ttl=300)
def fetch_reception_costs():
    """Obtiene costos de recepciones con cache"""
    costs_map = {}
    all_receptions = []
    offset = 0
    limit = 50
    
    while True:
        url = f"{BASE_URL}/stocks/receptions.json?limit={limit}&offset={offset}"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            break
        data = resp.json()
        items = data.get('items', [])
        if not items:
            break
        all_receptions.extend(items)
        if len(items) < limit:
            break
        offset += limit
    
    all_receptions.sort(key=lambda x: x.get('admissionDate', 0), reverse=True)
    
    for item in all_receptions:
        reception_id = item.get('id')
        details_url = f"{BASE_URL}/stocks/receptions/{reception_id}/details.json"
        d_resp = requests.get(details_url, headers=HEADERS, timeout=30)
        if d_resp.status_code == 200:
            d_data = d_resp.json()
            d_items = d_data.get('items', [])
            for d in d_items:
                v_id = str(d.get('variant', {}).get('id', ''))
                cost = d.get('cost', 0)
                if v_id and cost > 0 and v_id not in costs_map:
                    costs_map[v_id] = cost
    
    return costs_map

# ==================== EXTRACCIÓN PDF ====================
def extract_pdf_data(pdf_file):
    """Extrae modelo y precio del PDF de lista VIP"""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_file) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() + "\n"
        
        pattern = r'^\$(\d+(?:\.\d+)?)\s+([A-Z]\d+)\s+(\d+(?:\([^)]*\))?)\s+(.*?)$'
        
        data = []
        lines = text.split('\n')
        for line in lines:
            match = re.match(pattern, line.strip())
            if match:
                price, model, pieces, rest = match.groups()
                name_parts = rest.split(' ')
                name = name_parts[0] if name_parts else ''
                
                data.append({
                    'modelo': model.strip(),
                    'precio_nuevo': float(price.strip()),
                    'piezas_caja': pieces.strip(),
                    'nombre': name.strip()
                })
        
        return data
    except Exception as e:
        st.error(f"Error leyendo PDF: {e}")
        return []

# ==================== ARQUEO ====================
def do_arqueo(pdf_data, bsale_df, reception_costs):
    """Realiza el arqueo completo"""
    matches = []
    not_found = []
    
    for pdf_item in pdf_data:
        model = pdf_item['modelo']
        nuevo_precio = pdf_item['precio_nuevo']
        
        matching = bsale_df[bsale_df['product_name'].str.contains(model, case=False, na=False)]
        
        if not matching.empty:
            for _, row in matching.iterrows():
                stock = row['stock']
                variant_id = str(row['variant_id'])
                costo_viejo = reception_costs.get(variant_id, row['costo_promedio'])
                
                if stock > 0 and costo_viejo > 0 and costo_viejo != nuevo_precio:
                    valor_viejo = stock * costo_viejo
                    valor_nuevo = stock * nuevo_precio
                    matches.append({
                        'modelo_pdf': model,
                        'producto_bsale': row['product_name'],
                        'variante': row['variant_desc'],
                        'codigo': row['variant_code'],
                        'stock': stock,
                        'costo_viejo': costo_viejo,
                        'precio_nuevo': nuevo_precio,
                        'valor_viejo': valor_viejo,
                        'valor_nuevo': valor_nuevo,
                        'diferencia': valor_nuevo - valor_viejo,
                        'necesita_ajuste': True,
                        'match': True,
                        'status': '🔴 NECESITA AJUSTE'
                    })
                elif stock > 0 and costo_viejo > 0 and costo_viejo == nuevo_precio:
                    matches.append({
                        'modelo_pdf': model,
                        'producto_bsale': row['product_name'],
                        'variante': row['variant_desc'],
                        'codigo': row['variant_code'],
                        'stock': stock,
                        'costo_viejo': costo_viejo,
                        'precio_nuevo': nuevo_precio,
                        'valor_viejo': stock * costo_viejo,
                        'valor_nuevo': stock * nuevo_precio,
                        'diferencia': 0,
                        'necesita_ajuste': False,
                        'match': True,
                        'status': '🟢 YA ACTUALIZADO'
                    })
                else:
                    matches.append({
                        'modelo_pdf': model,
                        'producto_bsale': row['product_name'],
                        'variante': row['variant_desc'],
                        'codigo': row['variant_code'],
                        'stock': stock,
                        'costo_viejo': costo_viejo,
                        'precio_nuevo': nuevo_precio,
                        'valor_viejo': 0,
                        'valor_nuevo': 0,
                        'diferencia': 0,
                        'necesita_ajuste': False,
                        'match': True,
                        'status': '⚪ SIN STOCK'
                    })
        else:
            not_found.append({
                'modelo_pdf': model,
                'precio_nuevo': nuevo_precio,
                'piezas_caja': pdf_item['piezas_caja'],
                'nombre': pdf_item['nombre'],
                'status': '❌ NO ENCONTRADO'
            })
    
    return pd.DataFrame(matches), pd.DataFrame(not_found)

# ==================== UI ====================
st.markdown('<div class="main-header">📊 Bsale — Arqueo de Precios (PDF)</div>', unsafe_allow_html=True)
st.markdown('<div class="subheader">Compara lista VIP de proveedor con tu inventario Bsale y genera nota de crédito</div>', unsafe_allow_html=True)

# --- PASO 1: CARGAR PRODUCTOS BSALE ---
if 'bsale_loaded' not in st.session_state:
    st.session_state.bsale_loaded = False
if 'bsale_df' not in st.session_state:
    st.session_state.bsale_df = None
if 'reception_costs' not in st.session_state:
    st.session_state.reception_costs = {}

if not st.session_state.bsale_loaded:
    st.markdown('<div class="section-title">⚡ Cargar Inventario Bsale</div>', unsafe_allow_html=True)
    st.info("⏳ Primero cargamos tu catálogo de Bsale. Esto solo se hace una vez por sesión.")
    
    if st.button("🔄 Cargar Productos Bsale", type="primary", use_container_width=True):
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        status_text.text("Cargando productos de Bsale...")
        progress_bar.progress(20)
        bsale_df = fetch_all_products()
        st.session_state.bsale_df = bsale_df
        
        status_text.text("Cargando costos de recepciones...")
        progress_bar.progress(60)
        reception_costs = fetch_reception_costs()
        st.session_state.reception_costs = reception_costs
        
        progress_bar.progress(100)
        status_text.text("✅ Listo!")
        st.session_state.bsale_loaded = True
        
        st.success(f"✅ **{len(bsale_df)}** productos cargados | **{len(reception_costs)}** costos de recepción encontrados")
        st.rerun()
else:
    # --- PASO 2: SUBIR PDF ---
    st.markdown('<div class="section-title">📁 Subir PDF de Lista VIP</div>', unsafe_allow_html=True)
    st.success(f"✅ Bsale conectado: **{len(st.session_state.bsale_df)}** productos | **{len(st.session_state.reception_costs)}** costos de recepción")
    
    uploaded_file = st.file_uploader("Selecciona el PDF (lista VIP de proveedor)", type=['pdf'])
    
    if uploaded_file:
        with st.spinner("Extrayendo datos del PDF..."):
            pdf_data = extract_pdf_data(io.BytesIO(uploaded_file.read()))
        
        if pdf_data:
            st.success(f"✅ **{len(pdf_data)}** modelos encontrados en el PDF")
            
            df_preview = pd.DataFrame(pdf_data)
            st.dataframe(df_preview, use_container_width=True, height=180)
            
            st.markdown('<div class="section-title">🔍 Arquear</div>', unsafe_allow_html=True)
            
            if st.button("🚀 ARQUEAR AHORA", type="primary", use_container_width=True):
                with st.spinner("Analizando..."):
                    results, not_found = do_arqueo(pdf_data, st.session_state.bsale_df, st.session_state.reception_costs)
                
                # --- RESUMEN ---
                st.markdown('<div class="section-title">📊 Resumen del Arqueo</div>', unsafe_allow_html=True)
                
                necesita_ajuste = results[results['necesita_ajuste'] == True]
                ya_actualizado = results[(results['match'] == True) & (results['necesita_ajuste'] == False) & (results['stock'] > 0)]
                sin_stock = results[(results['match'] == True) & (results['stock'] == 0)]
                total_diferencia = necesita_ajuste['diferencia'].sum() if len(necesita_ajuste) > 0 else 0
                
                m1, m2, m3, m4 = st.columns(4)
                m1.markdown(f'<div class="metric-card metric-card-red"><div class="metric-value">{len(necesita_ajuste)}</div><div class="metric-label">🔴 Necesitan Ajuste</div></div>', unsafe_allow_html=True)
                m2.markdown(f'<div class="metric-card metric-card-green"><div class="metric-value">{len(ya_actualizado)}</div><div class="metric-label">🟢 Ya Actualizados</div></div>', unsafe_allow_html=True)
                m3.markdown(f'<div class="metric-card metric-card-gray"><div class="metric-value">{len(sin_stock)}</div><div class="metric-label">⚪ Sin Stock</div></div>', unsafe_allow_html=True)
                m4.markdown(f'<div class="metric-card metric-card-orange"><div class="metric-value">${total_diferencia:,.2f}</div><div class="metric-label">💰 Diferencia Total</div></div>', unsafe_allow_html=True)
                
                # --- NO ENCONTRADOS (PRIMERO) ---
                if not not_found.empty:
                    st.markdown('<div class="section-title">❌ NO ENCONTRADOS EN BSALE</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="not-found-box"><strong>⚠️ {len(not_found)} modelos</strong> del PDF no se encontraron en Bsale. Revisa si los nombres/códigos coinciden.</div>', unsafe_allow_html=True)
                    
                    not_found_display = not_found.copy()
                    not_found_display['precio_nuevo'] = not_found_display['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                    st.dataframe(
                        not_found_display[['modelo_pdf', 'nombre', 'precio_nuevo', 'piezas_caja']],
                        use_container_width=True,
                        height=200
                    )
                
                # --- DETALLE POR TABS ---
                st.markdown('<div class="section-title">📋 Detalle del Arqueo</div>', unsafe_allow_html=True)
                
                tab1, tab2, tab3 = st.tabs(["🔴 Necesitan Ajuste", "🟢 Ya Actualizados", "⚪ Sin Stock"])
                
                with tab1:
                    if not necesita_ajuste.empty:
                        st.markdown(f'<div class="highlight-box"><strong>{len(necesita_ajuste)} productos</strong> necesitan nota de crédito. Stock tiene costo viejo diferente al precio nuevo del PDF.</div>', unsafe_allow_html=True)
                        
                        df_display = necesita_ajuste.copy()
                        df_display['costo_viejo'] = df_display['costo_viejo'].apply(lambda x: f"${x:,.2f}")
                        df_display['precio_nuevo'] = df_display['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                        df_display['valor_viejo'] = df_display['valor_viejo'].apply(lambda x: f"${x:,.2f}")
                        df_display['valor_nuevo'] = df_display['valor_nuevo'].apply(lambda x: f"${x:,.2f}")
                        df_display['diferencia'] = df_display['diferencia'].apply(lambda x: f"${x:,.2f}")
                        
                        st.dataframe(
                            df_display[['modelo_pdf', 'producto_bsale', 'variante', 'codigo', 'stock', 'costo_viejo', 'precio_nuevo', 'valor_viejo', 'valor_nuevo', 'diferencia']],
                            use_container_width=True,
                            height=400
                        )
                        
                        # NOTA DE CRÉDITO
                        st.markdown('<div class="section-title">📝 Nota de Crédito</div>', unsafe_allow_html=True)
                        total_viejo = necesita_ajuste['valor_viejo'].sum()
                        total_nuevo = necesita_ajuste['valor_nuevo'].sum()
                        ajuste = total_nuevo - total_viejo
                        
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Valor Inventario Viejo", f"${total_viejo:,.2f}")
                        col2.metric("Valor Inventario Nuevo", f"${total_nuevo:,.2f}")
                        col3.metric("Ajuste / Nota de Crédito", f"${ajuste:,.2f}")
                        
                        if ajuste > 0:
                            st.info(f"📈 El inventario aumentaría de valor en **${ajuste:,.2f}**")
                        elif ajuste < 0:
                            st.info(f"📉 El inventario disminuiría de valor en **${abs(ajuste):,.2f}**")
                    else:
                        st.success("✅ Ningún producto necesita ajuste.")
                
                with tab2:
                    if not ya_actualizado.empty:
                        st.info(f"ℹ️ **{len(ya_actualizado)}** productos ya tienen el precio nuevo. No necesitan ajuste.")
                        df_display = ya_actualizado.copy()
                        df_display['costo_viejo'] = df_display['costo_viejo'].apply(lambda x: f"${x:,.2f}")
                        df_display['precio_nuevo'] = df_display['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                        st.dataframe(
                            df_display[['modelo_pdf', 'producto_bsale', 'variante', 'stock', 'costo_viejo', 'precio_nuevo']],
                            use_container_width=True,
                            height=300
                        )
                    else:
                        st.info("No hay productos ya actualizados.")
                
                with tab3:
                    if not sin_stock.empty:
                        st.info(f"ℹ️ **{len(sin_stock)}** productos encontrados en Bsale pero sin stock actual.")
                        df_display = sin_stock.copy()
                        st.dataframe(
                            df_display[['modelo_pdf', 'producto_bsale', 'variante', 'codigo']],
                            use_container_width=True,
                            height=200
                        )
                    else:
                        st.info("No hay productos sin stock.")
                
                # --- DESCARGAR CSV ---
                if not results.empty:
                    csv = results.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        "⬇️ Descargar Arqueo (CSV)",
                        csv,
                        f"arqueo_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                        "text/csv",
                        use_container_width=True
                    )
                
                if not not_found.empty:
                    csv_nf = not_found.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        "⬇️ Descargar NO Encontrados (CSV)",
                        csv_nf,
                        f"no_encontrados_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                        "text/csv",
                        use_container_width=True
                    )
        else:
            st.error("❌ No se pudieron extraer modelos del PDF. Verifica el formato.")

st.markdown("""
<div style="text-align: center; color: #888; margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e9ecef;">
    Bsale Arqueo PDF | Optimizado para velocidad
</div>
""", unsafe_allow_html=True)
