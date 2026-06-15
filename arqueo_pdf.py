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
        reception_date = item.get('receptionDate', '') or item.get('date', '')
        details = item.get('details', {})
        if isinstance(details, dict) and 'items' in details:
            for d in details.get('items', []):
                d['reception_date'] = reception_date
                all_details.append(d)
        elif isinstance(details, dict) and 'href' in details:
            # fallback si expand no funcionó
            d_data = get_json(details['href'])
            for d in d_data.get('items', []):
                d['reception_date'] = reception_date
                all_details.append(d)
    
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

def get_reception_history_for_variants(needed_variant_ids):
    """Obtiene el HISTORIAL COMPLETO de recepciones para cada variante — para calcular FIFO"""
    reception_history = {}
    needed = set(str(v) for v in needed_variant_ids)
    if not needed:
        return reception_history
    
    # Obtener conteo total de recepciones
    data = get_json(f"{BASE_URL}/stocks/receptions.json?limit=1&offset=0")
    total_count = data.get('count', 0)
    
    if total_count == 0:
        return reception_history
    
    limit = 50
    offsets = list(range(0, total_count, limit))
    
    # Progreso
    progress_bar = st.progress(0)
    status_text = st.empty()
    status_text.text(f"Obteniendo historial de recepciones...")
    
    with ThreadPoolExecutor(max_workers=RATE_LIMIT) as executor:
        futures = {executor.submit(fetch_reception_page, off, limit): off for off in offsets}
        
        completed = 0
        total_pages = len(offsets)
        
        for future in as_completed(futures):
            details, count = future.result()
            completed += 1
            progress_bar.progress(min(completed / total_pages, 1.0))
            
            for d in details:
                v_id = str(d.get('variant', {}).get('id', ''))
                cost = d.get('cost', 0)
                quantity = d.get('quantity', 0)
                reception_date = d.get('reception_date', '') or ''
                if v_id in needed and cost > 0 and quantity > 0:
                    if v_id not in reception_history:
                        reception_history[v_id] = []
                    reception_history[v_id].append({
                        'cost': cost,
                        'quantity': quantity,
                        'date': reception_date,
                    })
    
    progress_bar.empty()
    status_text.empty()
    return reception_history

def calculate_fifo_stock(stock_total, reception_history):
    """Calcula el desglose de stock por precio usando FIFO (por fecha)"""
    if not reception_history or stock_total <= 0:
        return []
    
    # Ordenar recepciones por fecha (más antigua primero) para FIFO verdadero
    sorted_receptions = sorted(reception_history, key=lambda x: x.get('date', '') or '9999-99-99')
    
    remaining = stock_total
    fifo_result = []
    
    # Aplicar FIFO: consumir desde las recepciones más antiguas
    for reception in sorted_receptions:
        if remaining <= 0:
            break
        qty = reception['quantity']
        cost = reception['cost']
        if remaining >= qty:
            # Toda esta recepción se consumió (vendida)
            remaining -= qty
        else:
            # Solo queda parte de esta recepción
            fifo_result.append({
                'cost': cost,
                'quantity': remaining,
                'date': reception.get('date', ''),
            })
            remaining = 0
    
    # Si aún queda stock después de consumir todas las recepciones antiguas,
    # es de la última recepción (la más reciente)
    if remaining > 0 and sorted_receptions:
        last_reception = sorted_receptions[-1]
        fifo_result.append({
            'cost': last_reception['cost'],
            'quantity': remaining,
            'date': last_reception.get('date', ''),
        })
    
    return fifo_result

# ==================== EXTRACCIÓN PDF ====================
def extract_pdf_data(pdf_file):
    """Extrae modelo y precio del PDF de lista VIP usando tablas (extract_tables)"""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_file) as pdf:
            data = []
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        # Saltar filas vacías o de header
                        if not row or len(row) < 4:
                            continue
                        
                        # Detectar header / fila de transición
                        first_cell = (row[0] or '').strip()
                        second_cell = (row[1] or '').strip() if len(row) > 1 else ''
                        
                        # Saltar headers conocidos
                        if any(k in first_cell for k in ['图片', 'Foto', 'MOREKA', 'VIP']):
                            continue
                        if 'Los siguientes están agotados' in (row[0] or ''):
                            continue
                        if not second_cell:
                            continue
                        
                        # Columnas típicas:
                        # [0]=foto, [1]=precio/estado, [2]=modelo, [3]=cantidad, [4]=nombre, [5]=características
                        price_cell = second_cell
                        model = ''
                        pieces = ''
                        name = ''
                        is_agotado = False
                        price = 0.0
                        
                        # Detectar si es producto agotado
                        if 'agotado' in price_cell.lower():
                            is_agotado = True
                            # Modelo está en la siguiente columna (índice 2)
                            if len(row) > 2 and row[2]:
                                model = row[2].strip().replace('\n', ' ')
                            # Cantidad en índice 3
                            if len(row) > 3 and row[3]:
                                pieces = str(row[3]).strip().replace('\n', ' ')
                            # Nombre en índice 4
                            if len(row) > 4 and row[4]:
                                name = row[4].strip().replace('\n', ' ')
                        else:
                            # Producto con precio
                            # Extraer primer precio (puede tener escalas separadas por \n)
                            price_lines = price_cell.split('\n')
                            first_price_line = price_lines[0].strip()
                            
                            # Quitar $ y convertir a float
                            price_str = first_price_line.replace('$', '').replace(',', '.')
                            try:
                                price = float(price_str)
                            except ValueError:
                                continue  # No es una fila de producto válida
                            
                            # Modelo en índice 2
                            if len(row) > 2 and row[2]:
                                model = row[2].strip().replace('\n', ' ')
                            # Cantidad en índice 3
                            if len(row) > 3 and row[3]:
                                pieces = str(row[3]).strip().replace('\n', ' ')
                            # Nombre en índice 4
                            if len(row) > 4 and row[4]:
                                name = row[4].strip().replace('\n', ' ')
                        
                        # Validar que tengamos modelo
                        if not model:
                            continue
                        
                        # El modelo debe parecer un código (letras, números, guiones)
                        # No debe ser una palabra larga tipo "VENTILADOR" o "FOCOVENTILADOR"
                        # Patrón típico: FS-133, SF110, MT-021, K108, WE017, etc.
                        model_clean = model.replace('-', '').replace('_', '')
                        if not re.match(r'^[A-Za-z0-9]+$', model_clean):
                            continue
                        if len(model) > 15:
                            continue  # Probablemente es texto, no un código
                        
                        data.append({
                            'modelo': model,
                            'precio_nuevo': price,
                            'piezas_caja': pieces,
                            'nombre': name[:80],
                            'agotado': is_agotado,
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
                
                st.info(f"📊 Encontrados: {len(matches_df)} matches | {len(not_found)} no encontrados")
                
                if matches_df.empty or len(matches_df) == 0:
                    st.warning("⚠️ No se encontraron coincidencias entre el PDF y Bsale. Verifica los modelos.")
                else:
                    # Obtener historial completo de recepciones y calcular FIFO
                    # Asegurar que costo_viejo sea float
                    matches_df['costo_viejo'] = matches_df['costo_viejo'].astype(float)
                    matches_df['stock'] = matches_df['stock'].astype(float)
                    
                    variant_ids = matches_df['variant_id'].unique().tolist()
                    st.write(f"🔍 Buscando historial de recepciones para {len(variant_ids)} variantes...")
                    reception_history = get_reception_history_for_variants(variant_ids)
                    st.write(f"✅ Historial obtenido: {len(reception_history)} variantes con recepciones")
                    
                    # Calcular FIFO para cada variante
                    for idx, row in matches_df.iterrows():
                        v_id = str(row['variant_id'])
                        precio_nuevo = row['precio_nuevo']
                        stock_total = row['stock']
                        
                        if v_id in reception_history and stock_total > 0:
                            history = reception_history[v_id]
                            fifo_result = calculate_fifo_stock(stock_total, history)
                            
                            # Calcular stock viejo y nuevo basado en FIFO
                            stock_viejo = 0
                            stock_nuevo = 0
                            costo_viejo_promedio = 0
                            
                            for lot in fifo_result:
                                if abs(lot['cost'] - precio_nuevo) < 0.01:
                                    stock_nuevo += lot['quantity']
                                else:
                                    stock_viejo += lot['quantity']
                            
                            # Calcular costo promedio del stock viejo
                            if stock_viejo > 0:
                                viejo_lots = [lot for lot in fifo_result if abs(lot['cost'] - precio_nuevo) >= 0.01]
                                if viejo_lots:
                                    costo_viejo_promedio = sum(lot['cost'] * lot['quantity'] for lot in viejo_lots) / stock_viejo
                            
                            matches_df.loc[idx, 'stock_viejo'] = stock_viejo
                            matches_df.loc[idx, 'stock_nuevo'] = stock_nuevo
                            matches_df.loc[idx, 'costo_viejo'] = costo_viejo_promedio if costo_viejo_promedio > 0 else row['costo_viejo']
                            matches_df.loc[idx, 'tipo_stock'] = 'VIEJO' if stock_viejo > 0 else 'NUEVO' if stock_nuevo > 0 else 'SIN RECEPCIÓN'
                        else:
                            matches_df.loc[idx, 'stock_viejo'] = stock_total
                            matches_df.loc[idx, 'stock_nuevo'] = 0
                            matches_df.loc[idx, 'tipo_stock'] = 'SIN RECEPCIÓN'
                    
                    # Calcular valores solo para stock viejo
                    matches_df['valor_viejo'] = matches_df['stock_viejo'] * matches_df['costo_viejo']
                    matches_df['valor_nuevo'] = matches_df['stock_nuevo'] * matches_df['precio_nuevo']
                    
                    # Diferencia: 0 si falta algún precio
                    matches_df['diferencia'] = 0.0
                    mask_valid = (matches_df['precio_nuevo'] > 0) & (matches_df['costo_viejo'] > 0)
                    matches_df.loc[mask_valid, 'diferencia'] = (
                        matches_df.loc[mask_valid, 'stock'] * matches_df.loc[mask_valid, 'precio_nuevo']
                    ) - (
                        matches_df.loc[mask_valid, 'stock'] * matches_df.loc[mask_valid, 'costo_viejo']
                    )
                    
                    # Subida de precio (solo cuando precio nuevo > precio viejo)
                    matches_df['subida'] = 0.0
                    mask_subida = mask_valid & (matches_df['precio_nuevo'] > matches_df['costo_viejo'])
                    matches_df.loc[mask_subida, 'subida'] = matches_df.loc[mask_subida, 'diferencia']
                    
                    # Bajada de precio (solo cuando precio nuevo < precio viejo)
                    matches_df['bajada'] = 0.0
                    mask_bajada = mask_valid & (matches_df['precio_nuevo'] < matches_df['costo_viejo'])
                    matches_df.loc[mask_bajada, 'bajada'] = matches_df.loc[mask_bajada, 'diferencia']
                    
                    matches_df['necesita_ajuste'] = (matches_df['stock_viejo'] > 0) & (matches_df['costo_viejo'] > 0)
                    
                    # Asegurar que 'offices' existe
                    if 'offices' not in matches_df.columns:
                        matches_df['offices'] = ''
                    
                    st.write(f"✅ Procesamiento completado. Mostrando resultados...")
                
                # --- RESUMEN ---
                st.markdown('<div class="section-title">📊 Resumen del Arqueo</div>', unsafe_allow_html=True)
                
                # Clasificar por tipo de stock
                stock_viejo_df = matches_df[matches_df['tipo_stock'] == 'VIEJO'] if 'tipo_stock' in matches_df.columns else pd.DataFrame()
                stock_nuevo_df = matches_df[matches_df['tipo_stock'] == 'NUEVO'] if 'tipo_stock' in matches_df.columns else pd.DataFrame()
                sin_recepcion_df = matches_df[matches_df['tipo_stock'] == 'SIN RECEPCIÓN'] if 'tipo_stock' in matches_df.columns else pd.DataFrame()
                sin_stock_df = matches_df[matches_df['stock'] == 0] if not matches_df.empty else pd.DataFrame()
                
                total_stock_viejo = stock_viejo_df['stock_viejo'].sum() if not stock_viejo_df.empty else 0
                total_stock_nuevo = stock_nuevo_df['stock_nuevo'].sum() if not stock_nuevo_df.empty else 0
                total_diferencia = stock_viejo_df['diferencia'].sum() if not stock_viejo_df.empty else 0
                
                m1, m2, m3, m4 = st.columns(4)
                m1.markdown(f'<div class="metric-card metric-card-red"><div class="metric-value">{len(stock_viejo_df)}</div><div class="metric-label">🔴 Stock Precio Viejo</div></div>', unsafe_allow_html=True)
                m2.markdown(f'<div class="metric-card metric-card-green"><div class="metric-value">{len(stock_nuevo_df)}</div><div class="metric-label">🟢 Stock Precio Nuevo</div></div>', unsafe_allow_html=True)
                m3.markdown(f'<div class="metric-card metric-card-gray"><div class="metric-value">{len(sin_stock_df)}</div><div class="metric-label">⚪ Sin Stock</div></div>', unsafe_allow_html=True)
                m4.markdown(f'<div class="metric-card metric-card-orange"><div class="metric-value">${total_diferencia:,.2f}</div><div class="metric-label">💰 Diferencia Total</div></div>', unsafe_allow_html=True)
                
                # Métricas adicionales de cantidades
                st.markdown('<div class="section-title">📦 Cantidades de Stock</div>', unsafe_allow_html=True)
                c1, c2, c3 = st.columns(3)
                c1.metric("Stock con Precio Viejo", f"{total_stock_viejo:,.0f} unidades")
                c2.metric("Stock con Precio Nuevo", f"{total_stock_nuevo:,.0f} unidades")
                c3.metric("Total Stock", f"{total_stock_viejo + total_stock_nuevo:,.0f} unidades")
                
                # --- NO ENCONTRADOS ---
                if not not_found.empty:
                    st.markdown('<div class="section-title">❌ NO ENCONTRADOS EN BSALE</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="not-found-box"><strong>⚠️ {len(not_found)} modelos</strong> del PDF no se encontraron en Bsale.</div>', unsafe_allow_html=True)
                    nf = not_found.copy()
                    nf['precio_nuevo'] = nf['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                    st.dataframe(nf[['modelo_pdf', 'nombre', 'precio_nuevo', 'piezas_caja']], use_container_width=True, height=180)
                
                # --- TABS ---
                st.markdown('<div class="section-title">📋 Detalle por Tipo de Stock</div>', unsafe_allow_html=True)
                tab1, tab2, tab3, tab4 = st.tabs(["🔴 Stock Precio Viejo", "🟢 Stock Precio Nuevo", "⚪ Sin Stock", "❓ Sin Recepción"])
                
                with tab1:
                    if not stock_viejo_df.empty:
                        st.markdown(f'<div class="highlight-box"><strong>{len(stock_viejo_df)}</strong> productos con stock a precio viejo</div>', unsafe_allow_html=True)
                        d = stock_viejo_df.copy()
                        d['costo_viejo'] = d['costo_viejo'].apply(lambda x: f"${x:,.2f}")
                        d['precio_nuevo'] = d['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                        d['valor_viejo'] = d['valor_viejo'].apply(lambda x: f"${x:,.2f}")
                        d['diferencia'] = d['diferencia'].apply(lambda x: f"${x:,.2f}")
                        d['subida'] = d['subida'].apply(lambda x: f"${x:,.2f}")
                        d['bajada'] = d['bajada'].apply(lambda x: f"${x:,.2f}")
                        st.dataframe(d[['modelo_pdf', 'producto_bsale', 'variante', 'stock_viejo', 'offices', 'costo_viejo', 'precio_nuevo', 'valor_viejo', 'diferencia', 'subida', 'bajada']], use_container_width=True, height=350)
                        
                        total_v = stock_viejo_df['valor_viejo'].sum()
                        total_d = stock_viejo_df['diferencia'].sum()
                        total_subida = stock_viejo_df['subida'].sum()
                        total_bajada = stock_viejo_df['bajada'].sum()
                        st.markdown('<div class="section-title">📝 Nota de Crédito (Stock Viejo)</div>', unsafe_allow_html=True)
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Valor Viejo", f"${total_v:,.2f}")
                        c2.metric("Diferencia Total", f"${total_d:,.2f}")
                        c3.metric("Subida de Precio", f"${total_subida:,.2f}")
                        c4.metric("Bajada de Precio", f"${total_bajada:,.2f}")
                    else:
                        st.success("✅ Ningún stock con precio viejo.")
                
                with tab2:
                    if not stock_nuevo_df.empty:
                        st.info(f"ℹ️ **{len(stock_nuevo_df)}** productos ya con precio nuevo")
                        d = stock_nuevo_df.copy()
                        d['precio_nuevo'] = d['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                        st.dataframe(d[['modelo_pdf', 'producto_bsale', 'variante', 'stock_nuevo', 'offices', 'precio_nuevo']], use_container_width=True, height=250)
                    else:
                        st.info("No hay stock con precio nuevo.")
                
                with tab3:
                    if not sin_stock_df.empty:
                        st.info(f"ℹ️ **{len(sin_stock_df)}** sin stock")
                        st.dataframe(sin_stock_df[['modelo_pdf', 'producto_bsale', 'variante', 'offices']], use_container_width=True, height=180)
                    else:
                        st.info("No hay.")
                
                with tab4:
                    if not sin_recepcion_df.empty:
                        st.warning(f"⚠️ **{len(sin_recepcion_df)}** productos sin historial de recepción")
                        st.dataframe(sin_recepcion_df[['modelo_pdf', 'producto_bsale', 'variante', 'stock', 'offices']], use_container_width=True, height=180)
                    else:
                        st.info("Todos los productos tienen historial de recepción.")
                
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
                    not_found_export['subida'] = 0
                    not_found_export['bajada'] = 0
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
