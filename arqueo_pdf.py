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
RATE_LIMIT = 10  # requests per second (Bsale limit)

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
.highlight-box { background: #fff3cd; border-left: 4px solid #ffc107; padding: 1rem; border-radius: 8px; margin: 0.5rem 0; }
.not-found-box { background: #f8d7da; border-left: 4px solid #dc3545; padding: 1rem; border-radius: 8px; margin: 0.5rem 0; }
</style>
""", unsafe_allow_html=True)

# ==================== API HELPERS ====================
def get_json(url):
    """Helper para GET requests"""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code == 200:
        return resp.json()
    return {}

def fetch_page(offset, limit=50):
    """Fetch una página de productos con variantes"""
    url = f"{BASE_URL}/products.json?limit={limit}&offset={offset}&expand=variants"
    data = get_json(url)
    items = data.get('items', [])
    
    results = []
    for item in items:
        prod_name = item.get('name', '')
        variants = item.get('variants', {}).get('items', [])
        if not variants:
            var_url = f"{BASE_URL}/products/{item.get('id')}/variants.json"
            v_data = get_json(var_url)
            variants = v_data.get('items', [])
        
        for v in variants:
            results.append({
                'product_name': prod_name,
                'variant_id': v.get('id'),
                'variant_desc': v.get('description', ''),
                'variant_code': v.get('code', ''),
                'stock': v.get('stock', 0),
                'costo_promedio': v.get('averageCost', 0)
            })
    
    return results, len(items)

def fetch_reception_page(offset, limit=50):
    """Fetch una página de recepciones con details inline"""
    url = f"{BASE_URL}/stocks/receptions.json?limit={limit}&offset={offset}&expand=[details]"
    data = get_json(url)
    items = data.get('items', [])
    
    all_details = []
    for item in items:
        details = item.get('details', {})
        if isinstance(details, dict) and 'items' in details:
            all_details.extend(details.get('items', []))
        elif isinstance(details, dict) and 'href' in details:
            # fallback si expand no funcionó
            d_data = get_json(details['href'])
            all_details.extend(d_data.get('items', []))
    
    return all_details, len(items)

def fetch_stock_page(offset, limit=50):
    """Fetch una página de stocks con oficina y variante"""
    url = f"{BASE_URL}/stocks.json?limit={limit}&offset={offset}&expand=variant,office"
    data = get_json(url)
    items = data.get('items', [])
    
    results = []
    for item in items:
        variant = item.get('variant', {})
        office = item.get('office', {})
        quantity = item.get('quantity', 0) or 0
        qty_available = item.get('quantityAvailable', 0) or 0
        
        if variant and quantity > 0:
            results.append({
                'variant_id': variant.get('id'),
                'variant_code': variant.get('code', ''),
                'variant_desc': variant.get('description', ''),
                'product_id': variant.get('product', {}).get('id', ''),
                'office_id': office.get('id', ''),
                'office_name': office.get('name', ''),
                'quantity': quantity,
                'quantityAvailable': qty_available,
            })
    
    return results, len(items)

def get_total_stock_by_variant():
    """Obtiene stock total de TODAS las oficinas agrupado por variante"""
    data = get_json(f"{BASE_URL}/stocks.json?limit=1&offset=0")
    total_count = data.get('count', 0)
    
    if total_count == 0:
        return {}
    
    limit = 50
    offsets = list(range(0, total_count, limit))
    
    all_stocks = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_stock_page, off, limit): off for off in offsets}
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        status_text.text(f"Obteniendo stock de {total_count} registros...")
        
        completed = 0
        total = len(offsets)
        
        for future in as_completed(futures):
            results, count = future.result()
            all_stocks.extend(results)
            completed += 1
            progress_bar.progress(min(completed / total, 1.0))
        
        progress_bar.empty()
        status_text.empty()
    
    # Agrupar por variant_id sumando quantity
    stock_by_variant = {}
    for stock in all_stocks:
        v_id = stock['variant_id']
        if v_id not in stock_by_variant:
            stock_by_variant[v_id] = {
                'total_quantity': 0,
                'total_available': 0,
                'offices': [],
                'variant_code': stock.get('variant_code', ''),
                'variant_desc': stock.get('variant_desc', ''),
            }
        stock_by_variant[v_id]['total_quantity'] += stock['quantity']
        stock_by_variant[v_id]['total_available'] += stock['quantityAvailable']
        stock_by_variant[v_id]['offices'].append(stock['office_name'])
    
    return stock_by_variant

def fetch_all_products_fast():
    """Obtiene todos los productos con stock REAL de todas las oficinas"""
    data = get_json(f"{BASE_URL}/products.json?limit=1&offset=0")
    total_count = data.get('count', 0)
    
    if total_count == 0:
        return pd.DataFrame()
    
    # Paso 1: Obtener productos y variantes
    limit = 50
    offsets = list(range(0, total_count, limit))
    
    all_results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_page, off, limit): off for off in offsets}
        
        progress_bar = st.progress(0)
        completed = 0
        total = len(offsets)
        
        for future in as_completed(futures):
            results, count = future.result()
            all_results.extend(results)
            completed += 1
            progress_bar.progress(min(completed / total, 1.0))
        
        progress_bar.empty()
    
    df = pd.DataFrame(all_results)
    
    # Paso 2: Obtener stock de todas las oficinas
    stock_by_variant = get_total_stock_by_variant()
    
    # Paso 3: Mergear stock real
    df['stock'] = df['variant_id'].apply(lambda v: stock_by_variant.get(v, {}).get('total_quantity', 0) if v in stock_by_variant else 0)
    df['stock_available'] = df['variant_id'].apply(lambda v: stock_by_variant.get(v, {}).get('total_available', 0) if v in stock_by_variant else 0)
    df['offices'] = df['variant_id'].apply(lambda v: ', '.join(stock_by_variant.get(v, {}).get('offices', [])) if v in stock_by_variant else '')
    
    return df

def get_reception_costs_for_variants(needed_variant_ids):
    """Obtiene costos de recepción SOLO para variantes necesarias — ultra rápido"""
    costs_map = {}
    needed = set(str(v) for v in needed_variant_ids)
    if not needed:
        return costs_map
    
    # Obtener conteo total de recepciones
    data = get_json(f"{BASE_URL}/stocks/receptions.json?limit=1&offset=0")
    total_count = data.get('count', 0)
    
    if total_count == 0:
        return costs_map
    
    limit = 50
    offsets = list(range(0, total_count, limit))
    
    found_count = 0
    total_needed = len(needed)
    
    # Progreso
    progress_bar = st.progress(0)
    status_text = st.empty()
    status_text.text(f"Buscando costos en {total_count} recepciones...")
    
    with ThreadPoolExecutor(max_workers=RATE_LIMIT) as executor:
        futures = {executor.submit(fetch_reception_page, off, limit): off for off in offsets}
        
        completed = 0
        total_pages = len(offsets)
        
        for future in as_completed(futures):
            details, count = future.result()
            completed += 1
            progress_bar.progress(min(completed / total_pages, 1.0))
            status_text.text(f"Procesando página {completed}/{total_pages}... ({found_count}/{total_needed} encontrados)")
            
            for d in details:
                v_id = str(d.get('variant', {}).get('id', ''))
                cost = d.get('cost', 0)
                if v_id in needed and cost > 0 and v_id not in costs_map:
                    costs_map[v_id] = cost
                    found_count += 1
            
            # Early stop: si ya encontramos todos los que necesitamos
            if found_count >= total_needed:
                # Cancelar futuros pendientes
                for f in futures:
                    f.cancel()
                break
    
    progress_bar.empty()
    status_text.empty()
    return costs_map

# ==================== EXTRACCIÓN PDF ====================
def extract_pdf_data(pdf_file):
    """Extrae modelo y precio del PDF de lista VIP — soporta múltiples formatos"""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_file) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() + "\n"
        
        data = []
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Detectar si es línea de agotado o con precio
            is_agotado = line.lower().startswith('agotado')
            
            if is_agotado:
                # Formato: agotado [MODELO] [cantidad] [nombre]
                tokens = line.split()
                if len(tokens) < 3:
                    continue
                model = tokens[1]
                # Cantidad: tercer token (número, posiblemente con paréntesis como 60(4))
                qty_token = tokens[2]
                if not re.match(r'\d+(?:\([^)]*\))?', qty_token):
                    continue
                pieces = qty_token
                name = ' '.join(tokens[3:])
                # Precio 0 para agotados (se marcarán como no disponible)
                price = 0.0
                
            else:
                # Formato: $[precio] [opcional: escala] [MODELO] [cantidad] [nombre]
                if not line.startswith('$'):
                    continue
                
                tokens = line.split()
                if len(tokens) < 4:
                    continue
                
                # Precio: primer token (quitar $)
                price_str = tokens[0].replace('$', '').replace(',', '.')
                try:
                    price = float(price_str)
                except:
                    continue
                
                # Buscar el modelo: es el token que parece un modelo (formato: letras+guiones+números)
                # y NO es escala y NO es cantidad
                # Escala: "50cajas:$130" o "100cajas:125"
                model = None
                pieces = None
                name_start_idx = None
                
                for i, token in enumerate(tokens[1:], start=1):
                    # Saltar tokens de escala
                    if re.match(r'\d+cajas:', token, re.IGNORECASE):
                        continue
                    # El modelo coincide con patrón: empieza con letra, tiene letras/números/guiones, longitud 2-10
                    if model is None and re.match(r'^[A-Za-z][A-Za-z0-9\-]{1,10}$', token):
                        model = token
                        continue
                    # La cantidad es el primer número después del modelo
                    if model is not None and pieces is None:
                        if re.match(r'\d+(?:\([^)]*\))?', token):
                            pieces = token
                            name_start_idx = i + 1
                            break
                
                if model is None or pieces is None:
                    continue
                
                name = ' '.join(tokens[name_start_idx:]) if name_start_idx else ''
            
            if model:
                data.append({
                    'modelo': model.strip(),
                    'precio_nuevo': price,
                    'piezas_caja': pieces.strip(),
                    'nombre': name.strip()[:50],  # Limitar nombre
                    'agotado': is_agotado
                })
        
        return data
    except Exception as e:
        st.error(f"Error leyendo PDF: {e}")
        return []

# ==================== ARQUEO ====================
def normalize_model(model):
    """Normaliza un modelo para matching flexible: quita guiones, espacios, mayúsculas"""
    return re.sub(r'[^a-zA-Z0-9]', '', model).upper()

def do_arqueo_fast(pdf_data, bsale_df):
    """Arqueo optimizado — matching en memoria con normalización"""
    matches = []
    not_found = []
    
    # Pre-construir diccionario de modelo normalizado → variantes
    model_to_variants = {}
    for _, row in bsale_df.iterrows():
        name = row['product_name'].upper()
        # También crear versión sin guiones del nombre para buscar
        name_normalized = normalize_model(name)
        
        for pdf_item in pdf_data:
            model = pdf_item['modelo']
            model_norm = normalize_model(model)
            
            # Intentar match de 3 formas:
            # 1. Modelo exacto en nombre
            # 2. Modelo normalizado en nombre normalizado
            # 3. Modelo sin guiones en nombre sin guiones
            match_found = False
            
            if model.upper() in name:
                match_found = True
            elif model_norm in name_normalized:
                match_found = True
            elif len(model_norm) >= 3:  # Solo buscar substrings si el modelo es largo
                # Buscar el modelo sin guiones dentro del nombre sin guiones
                if model_norm in name_normalized:
                    match_found = True
            
            if match_found:
                if model not in model_to_variants:
                    model_to_variants[model] = []
                model_to_variants[model].append(row)
    
    for pdf_item in pdf_data:
        model = pdf_item['modelo']
        nuevo_precio = pdf_item['precio_nuevo']
        agotado = pdf_item.get('agotado', False)
        
        if model in model_to_variants:
            for row in model_to_variants[model]:
                matches.append({
                    'modelo_pdf': model,
                    'producto_bsale': row['product_name'],
                    'variante': row['variant_desc'],
                    'codigo': row['variant_code'],
                    'stock': row['stock'],
                    'offices': row.get('offices', ''),
                    'variant_id': str(row['variant_id']),
                    'precio_nuevo': nuevo_precio,
                    'costo_viejo': row['costo_promedio'],
                    'agotado': agotado,
                })
        else:
            not_found.append({
                'modelo_pdf': model,
                'precio_nuevo': nuevo_precio,
                'piezas_caja': pdf_item['piezas_caja'],
                'nombre': pdf_item['nombre'],
                'status': '❌ NO ENCONTRADO',
                'agotado': agotado,
            })
    
    return pd.DataFrame(matches), pd.DataFrame(not_found)

# ==================== UI ====================
st.markdown('<div class="main-header">📊 Bsale — Arqueo de Precios (PDF)</div>', unsafe_allow_html=True)
st.markdown('<div class="subheader">Compara lista VIP de proveedor con tu inventario Bsale y genera nota de crédito</div>', unsafe_allow_html=True)

if 'bsale_df' not in st.session_state:
    st.session_state.bsale_df = None
if 'step' not in st.session_state:
    st.session_state.step = 1

# --- PASO 1: CARGAR BSALE ---
if st.session_state.step == 1:
    st.markdown('<div class="section-title">⚡ Paso 1: Cargar Inventario Bsale</div>', unsafe_allow_html=True)
    
    if st.button("🔄 Cargar Productos Bsale (Paralelo)", type="primary", use_container_width=True):
        with st.spinner("Cargando catálogo completo..."):
            bsale_df = fetch_all_products_fast()
            st.session_state.bsale_df = bsale_df
            st.session_state.step = 2
        st.success(f"✅ **{len(bsale_df)}** variantes cargadas")
        st.rerun()

# --- PASO 2: SUBIR PDF Y ARQUEAR ---
else:
    st.markdown('<div class="section-title">📁 Paso 2: Subir PDF de Lista VIP</div>', unsafe_allow_html=True)
    st.success(f"✅ Bsale conectado: **{len(st.session_state.bsale_df)}** variantes cargadas")
    
    uploaded_files = st.file_uploader("Selecciona los PDFs (lista VIP de proveedor)", type=['pdf'], accept_multiple_files=True)
    
    if uploaded_files:
        all_pdf_data = []
        pdf_stats = []
        
        for pdf_file in uploaded_files:
            with st.spinner(f"Extrayendo datos de {pdf_file.name}..."):
                pdf_data = extract_pdf_data(io.BytesIO(pdf_file.read()))
                all_pdf_data.extend(pdf_data)
                pdf_stats.append({
                    'archivo': pdf_file.name,
                    'total': len(pdf_data),
                    'disponibles': sum(1 for d in pdf_data if not d['agotado']),
                    'agotados': sum(1 for d in pdf_data if d['agotado'])
                })
        
        if all_pdf_data:
            st.success(f"✅ **{len(all_pdf_data)}** modelos encontrados en total ({len(uploaded_files)} PDFs)")
            
            # Mostrar resumen por PDF
            stats_df = pd.DataFrame(pdf_stats)
            st.dataframe(stats_df, use_container_width=True, height=150)
            
            # Mostrar preview de todos los modelos
            with st.expander("📋 Ver todos los modelos extraídos"):
                st.dataframe(pd.DataFrame(all_pdf_data), use_container_width=True, height=300)
            
            if st.button("🚀 ARQUEAR AHORA", type="primary", use_container_width=True):
                with st.spinner("Buscando coincidencias en Bsale..."):
                    matches_df, not_found = do_arqueo_fast(all_pdf_data, st.session_state.bsale_df)
                
                # Obtener costos de recepción SOLO para variantes encontradas
                if not matches_df.empty and len(matches_df) > 0:
                    # Asegurar que costo_viejo sea float
                    matches_df['costo_viejo'] = matches_df['costo_viejo'].astype(float)
                    matches_df['stock'] = matches_df['stock'].astype(float)
                    
                    variant_ids = matches_df['variant_id'].unique().tolist()
                    reception_costs = get_reception_costs_for_variants(variant_ids)
                    
                    # Aplicar costos de recepción
                    for idx, row in matches_df.iterrows():
                        v_id = str(row['variant_id'])
                        if v_id in reception_costs:
                            matches_df.loc[idx, 'costo_viejo'] = float(reception_costs[v_id])
                    
                    # Calcular
                    matches_df['valor_viejo'] = matches_df['stock'] * matches_df['costo_viejo']
                    matches_df['valor_nuevo'] = matches_df['stock'] * matches_df['precio_nuevo']
                    matches_df['diferencia'] = matches_df['valor_nuevo'] - matches_df['valor_viejo']
                    matches_df['necesita_ajuste'] = (matches_df['stock'] > 0) & (matches_df['costo_viejo'] > 0) & (matches_df['costo_viejo'] != matches_df['precio_nuevo'])
                
                # --- RESUMEN ---
                st.markdown('<div class="section-title">📊 Resumen del Arqueo</div>', unsafe_allow_html=True)
                
                necesita_ajuste = matches_df[matches_df['necesita_ajuste'] == True] if not matches_df.empty else pd.DataFrame()
                ya_actualizado = matches_df[(matches_df['stock'] > 0) & (~matches_df['necesita_ajuste'])] if not matches_df.empty else pd.DataFrame()
                sin_stock = matches_df[matches_df['stock'] == 0] if not matches_df.empty else pd.DataFrame()
                total_diferencia = necesita_ajuste['diferencia'].sum() if not necesita_ajuste.empty else 0
                
                m1, m2, m3, m4 = st.columns(4)
                m1.markdown(f'<div class="metric-card metric-card-red"><div class="metric-value">{len(necesita_ajuste)}</div><div class="metric-label">🔴 Necesitan Ajuste</div></div>', unsafe_allow_html=True)
                m2.markdown(f'<div class="metric-card metric-card-green"><div class="metric-value">{len(ya_actualizado)}</div><div class="metric-label">🟢 Ya Actualizados</div></div>', unsafe_allow_html=True)
                m3.markdown(f'<div class="metric-card metric-card-gray"><div class="metric-value">{len(sin_stock)}</div><div class="metric-label">⚪ Sin Stock</div></div>', unsafe_allow_html=True)
                m4.markdown(f'<div class="metric-card metric-card-orange"><div class="metric-value">${total_diferencia:,.2f}</div><div class="metric-label">💰 Diferencia Total</div></div>', unsafe_allow_html=True)
                
                # --- NO ENCONTRADOS ---
                if not not_found.empty:
                    st.markdown('<div class="section-title">❌ NO ENCONTRADOS EN BSALE</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="not-found-box"><strong>⚠️ {len(not_found)} modelos</strong> del PDF no se encontraron en Bsale.</div>', unsafe_allow_html=True)
                    nf = not_found.copy()
                    nf['precio_nuevo'] = nf['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                    st.dataframe(nf[['modelo_pdf', 'nombre', 'precio_nuevo', 'piezas_caja']], use_container_width=True, height=180)
                
                # --- TABS ---
                st.markdown('<div class="section-title">📋 Detalle</div>', unsafe_allow_html=True)
                tab1, tab2, tab3 = st.tabs(["🔴 Necesitan Ajuste", "🟢 Ya Actualizados", "⚪ Sin Stock"])
                
                with tab1:
                    if not necesita_ajuste.empty:
                        st.markdown(f'<div class="highlight-box"><strong>{len(necesita_ajuste)}</strong> necesitan nota de crédito</div>', unsafe_allow_html=True)
                        d = necesita_ajuste.copy()
                        d['costo_viejo'] = d['costo_viejo'].apply(lambda x: f"${x:,.2f}")
                        d['precio_nuevo'] = d['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                        d['valor_viejo'] = d['valor_viejo'].apply(lambda x: f"${x:,.2f}")
                        d['valor_nuevo'] = d['valor_nuevo'].apply(lambda x: f"${x:,.2f}")
                        d['diferencia'] = d['diferencia'].apply(lambda x: f"${x:,.2f}")
                        st.dataframe(d[['modelo_pdf', 'producto_bsale', 'variante', 'stock', 'offices', 'costo_viejo', 'precio_nuevo', 'valor_viejo', 'valor_nuevo', 'diferencia']], use_container_width=True, height=350)
                        
                        total_v = necesita_ajuste['valor_viejo'].sum()
                        total_n = necesita_ajuste['valor_nuevo'].sum()
                        ajuste = total_n - total_v
                        st.markdown('<div class="section-title">📝 Nota de Crédito</div>', unsafe_allow_html=True)
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Valor Viejo", f"${total_v:,.2f}")
                        c2.metric("Valor Nuevo", f"${total_n:,.2f}")
                        c3.metric("Ajuste", f"${ajuste:,.2f}")
                    else:
                        st.success("✅ Ninguno necesita ajuste.")
                
                with tab2:
                    if not ya_actualizado.empty:
                        st.info(f"ℹ️ **{len(ya_actualizado)}** ya actualizados")
                        d = ya_actualizado.copy()
                        d['costo_viejo'] = d['costo_viejo'].apply(lambda x: f"${x:,.2f}")
                        d['precio_nuevo'] = d['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                        st.dataframe(d[['modelo_pdf', 'producto_bsale', 'variante', 'stock', 'offices', 'costo_viejo', 'precio_nuevo']], use_container_width=True, height=250)
                    else:
                        st.info("No hay.")
                
                with tab3:
                    if not sin_stock.empty:
                        st.info(f"ℹ️ **{len(sin_stock)}** sin stock")
                        st.dataframe(sin_stock[['modelo_pdf', 'producto_bsale', 'variante', 'offices']], use_container_width=True, height=180)
                    else:
                        st.info("No hay.")
                
                # --- DESCARGA UNIFICADA ---
                # Preparar DataFrame unificado: encontrados + no encontrados
                # Encontrados: marcar como "Encontrado"
                matches_export = matches_df.copy() if not matches_df.empty else pd.DataFrame()
                if not matches_export.empty:
                    matches_export['estado'] = 'Encontrado'
                
                # No encontrados: agregar columnas vacías para que coincidan con el formato
                not_found_export = not_found.copy() if not not_found.empty else pd.DataFrame()
                if not not_found_export.empty:
                    not_found_export['estado'] = 'No Encontrado'
                    not_found_export['producto_bsale'] = ''
                    not_found_export['variante'] = ''
                    not_found_export['codigo'] = ''
                    not_found_export['stock'] = 0
                    not_found_export['variant_id'] = ''
                    not_found_export['costo_viejo'] = 0
                    not_found_export['valor_viejo'] = 0
                    not_found_export['valor_nuevo'] = 0
                    not_found_export['diferencia'] = 0
                    not_found_export['necesita_ajuste'] = False
                
                # Unir ambos
                if not matches_export.empty and not not_found_export.empty:
                    unified_df = pd.concat([matches_export, not_found_export], ignore_index=True)
                elif not matches_export.empty:
                    unified_df = matches_export
                elif not not_found_export.empty:
                    unified_df = not_found_export
                else:
                    unified_df = pd.DataFrame()
                
                if not unified_df.empty:
                    st.download_button(
                        "⬇️ Descargar Arqueo Completo (CSV)", 
                        unified_df.to_csv(index=False).encode('utf-8'), 
                        f"arqueo_completo_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", 
                        "text/csv", 
                        use_container_width=True
                    )
                
                # Descargas separadas (opcional, por si las necesita)
                if not matches_df.empty:
                    st.download_button("⬇️ Solo Encontrados (CSV)", matches_df.to_csv(index=False).encode('utf-8'), f"arqueo_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", "text/csv", use_container_width=True)
                if not not_found.empty:
                    st.download_button("⬇️ Solo No Encontrados (CSV)", not_found.to_csv(index=False).encode('utf-8'), f"no_encontrados_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", "text/csv", use_container_width=True)
        else:
            st.error("❌ No se extrajeron modelos del PDF.")

st.markdown("""<div style="text-align: center; color: #888; margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e9ecef;">Bsale Arqueo PDF | Ultra-rápido</div>""", unsafe_allow_html=True)
