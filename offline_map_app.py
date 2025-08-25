import sys
import base64
import os
import requests
import threading
import urllib.parse
from pathlib import Path
import urllib.request
import urllib.error
import http.server
import socketserver
from threading import Thread
import time
from PySide6.QtWidgets import QApplication, QMainWindow, QProgressBar, QVBoxLayout, QWidget
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QObject, Signal, Slot, QThread, QUrl
from PySide6.QtWebChannel import QWebChannel


class TileServer(Thread):
    """Yerel tile dosyalarını serve eden HTTP server"""
    def __init__(self, tiles_dir, port=8000):
        super().__init__(daemon=True)
        self.tiles_dir = tiles_dir
        self.port = port
        self.server = None
        
    def run(self):
        try:
            # Tiles dizinine geç
            os.chdir(self.tiles_dir)
            
            # HTTP server başlat
            handler = http.server.SimpleHTTPRequestHandler
            self.server = socketserver.TCPServer(("", self.port), handler)
            print(f"Tile server başlatıldı: http://localhost:{self.port}")
            self.server.serve_forever()
        except Exception as e:
            print(f"Tile server hatası: {e}")
    
    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


class OfflineManager:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.assets_dir = os.path.join(self.base_dir, 'assets')
        self.tiles_dir = os.path.join(self.base_dir, 'tiles', 'satellite')
        self.leaflet_dir = os.path.join(self.assets_dir, 'leaflet')
        
        # Tile server
        self.tile_server = None
        self.server_port = 8000
        
        # Dizinleri oluştur
        os.makedirs(self.leaflet_dir, exist_ok=True)
        os.makedirs(self.tiles_dir, exist_ok=True)
    
    def start_tile_server(self):
        """Yerel tile server'ını başlat"""
        if not self.tile_server:
            self.tile_server = TileServer(self.tiles_dir, self.server_port)
            self.tile_server.start()
            time.sleep(1)  # Server'ın başlaması için bekle
            return True
        return True
    
    def stop_tile_server(self):
        """Tile server'ını durdur"""
        if self.tile_server:
            self.tile_server.stop()
            self.tile_server = None
    
    def is_internet_available(self):
        """İnternet bağlantısını kontrol et"""
        try:
            urllib.request.urlopen('http://www.google.com', timeout=2)
            return True
        except:
            return False
    
    def download_leaflet_files(self):
        """Leaflet dosyalarını indir ve kaydet"""
        if not self.is_internet_available():
            return False
            
        files_to_download = [
            ('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js', 'leaflet.js'),
            ('https://unpkg.com/leaflet@1.9.4/dist/leaflet.css', 'leaflet.css')
        ]
        
        try:
            for url, filename in files_to_download:
                file_path = os.path.join(self.leaflet_dir, filename)
                if not os.path.exists(file_path):
                    print(f"İndiriliyor: {filename}")
                    urllib.request.urlretrieve(url, file_path)
            return True
        except Exception as e:
            print(f"Leaflet indirme hatası: {e}")
            return False
    
    def leaflet_files_exist(self):
        """Leaflet dosyaları mevcut mu kontrol et"""
        js_file = os.path.join(self.leaflet_dir, 'leaflet.js')
        css_file = os.path.join(self.leaflet_dir, 'leaflet.css')
        return os.path.exists(js_file) and os.path.exists(css_file)
    
    def get_leaflet_content(self):
        """Leaflet dosyalarının içeriğini döndür"""
        # Her zaman önce yerel dosyaları kontrol et
        if self.leaflet_files_exist():
            try:
                js_path = os.path.join(self.leaflet_dir, 'leaflet.js')
                css_path = os.path.join(self.leaflet_dir, 'leaflet.css')
                
                with open(js_path, 'r', encoding='utf-8') as f:
                    js_content = f.read()
                with open(css_path, 'r', encoding='utf-8') as f:
                    css_content = f.read()
                
                print("Leaflet yerel dosyalardan yüklendi")
                return js_content, css_content, True  # True = local content
            except Exception as e:
                print(f"Yerel Leaflet okuma hatası: {e}")
        
        # Yerel dosya yoksa ve internet varsa, indir
        if self.is_internet_available():
            print("Leaflet indiriliyor...")
            if self.download_leaflet_files():
                return self.get_leaflet_content()  # Recursive call after download
        
        # Son çare: CDN URL'leri (sadece internet varken çalışır)
        print("CDN'den Leaflet yükleniyor...")
        return ('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js', 
               'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css', False)
    
    def has_offline_tiles(self):
        """Offline tile'ların varlığını kontrol et"""
        # En az bir tile dosyasının varlığını kontrol et
        for zoom in [14, 15, 16, 17, 18]:
            zoom_dir = os.path.join(self.tiles_dir, str(zoom))
            if os.path.exists(zoom_dir):
                for x_dir in os.listdir(zoom_dir):
                    x_path = os.path.join(zoom_dir, x_dir)
                    if os.path.isdir(x_path):
                        tiles = [f for f in os.listdir(x_path) if f.endswith('.png')]
                        if tiles:
                            print(f"Offline tile'lar bulundu: {zoom}/{x_dir}/")
                            return True
        print("Hiç offline tile bulunamadı")
        return False


class TileDownloader(QThread):
    progress_updated = Signal(int, int)  # current, total
    download_finished = Signal(str)
    
    def __init__(self, center_lat, center_lon, radius=800):
        super().__init__()
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.radius = radius
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.tiles_dir = os.path.join(self.base_dir, 'tiles', 'satellite')
        os.makedirs(self.tiles_dir, exist_ok=True)
    
    def deg2num(self, lat_deg, lon_deg, zoom):
        """Koordinatları tile numaralarına çevir"""
        import math
        lat_rad = math.radians(lat_deg)
        n = 2.0 ** zoom
        xtile = int((lon_deg + 180.0) / 360.0 * n)
        ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
        return (xtile, ytile)
    
    def calculate_tile_bounds(self, lat, lon, radius_m, zoom):
        """Belirli yarıçaptaki tile sınırlarını hesapla"""
        import math
        
        # Yaklaşık 111320 metre = 1 derece (enlem için)
        meters_per_degree = 111320
        
        # Radius'u dereceye çevir
        lat_offset = radius_m / meters_per_degree
        lon_offset = radius_m / (meters_per_degree * math.cos(math.radians(lat)))
        
        # Sınırları hesapla
        north = lat + lat_offset
        south = lat - lat_offset  
        east = lon + lon_offset
        west = lon - lon_offset
        
        # Tile koordinatlarına çevir (düzeltildi: min/max doğru atanmış)
        nw_x, nw_y = self.deg2num(north, west, zoom)  # min_x, min_y
        se_x, se_y = self.deg2num(south, east, zoom)  # max_x, max_y
        
        min_x = nw_x
        min_y = nw_y
        max_x = se_x
        max_y = se_y
        
        # Eğer min > max olursa (nadir olsa da), düzelt
        if min_x > max_x:
            min_x, max_x = max_x, min_x
        if min_y > max_y:
            min_y, max_y = max_y, min_y
        
        return min_x, min_y, max_x, max_y
    
    def run(self):
        """Tile indirme işlemini çalıştır"""
        # Zoom seviyeleri: 14'ten 18'e kadar (sizin belirttiğiniz)
        zoom_levels = [14, 15, 16, 17, 18]
        total_tiles = 0
        downloaded_tiles = 0
        
        print(f"Merkez: {self.center_lat}, {self.center_lon}, Yarıçap: {self.radius}m")
        
        # Toplam tile sayısını hesapla
        all_tiles = []
        for zoom in zoom_levels:
            min_x, min_y, max_x, max_y = self.calculate_tile_bounds(
                self.center_lat, self.center_lon, self.radius, zoom
            )
            print(f"Zoom {zoom}: x={min_x}-{max_x}, y={min_y}-{max_y}")
            
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    all_tiles.append((zoom, x, y))
        
        total_tiles = len(all_tiles)
        print(f"Toplam indirilecek tile: {total_tiles}")
        
        # Tile'ları indir
        success_count = 0
        failed_count = 0
        
        for zoom, x, y in all_tiles:
            try:
                # Tile klasörünü oluştur
                tile_dir = os.path.join(self.tiles_dir, str(zoom), str(x))
                os.makedirs(tile_dir, exist_ok=True)
                
                tile_path = os.path.join(tile_dir, f'{y}.png')
                
                # Zaten varsa atla
                if os.path.exists(tile_path):
                    downloaded_tiles += 1
                    self.progress_updated.emit(downloaded_tiles, total_tiles)
                    continue
                
                # Tile'ı indir
                url = f'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{y}/{x}'
                
                response = requests.get(url, timeout=15)
                if response.status_code == 200:
                    with open(tile_path, 'wb') as f:
                        f.write(response.content)
                    success_count += 1
                    if success_count % 10 == 0:  # Her 10 başarılı indirmede log
                        print(f"✅ İndirildi: {success_count}/{total_tiles} tile")
                else:
                    failed_count += 1
                    print(f"❌ Hata {response.status_code}: {zoom}/{x}/{y}")
                
                downloaded_tiles += 1
                self.progress_updated.emit(downloaded_tiles, total_tiles)
                
            except Exception as e:
                failed_count += 1
                print(f"Tile indirme hatası {zoom}/{x}/{y}: {e}")
                downloaded_tiles += 1
                self.progress_updated.emit(downloaded_tiles, total_tiles)
        
        success_msg = f"İndirme tamamlandı: {success_count} başarılı, {failed_count} başarısız, Toplam: {total_tiles}"
        print(success_msg)
        self.download_finished.emit(success_msg)

    def find_downloaded_center(self):
        """İndirilen tile'lardan merkez koordinatı hesapla"""
        try:
            zoom16_path = os.path.join(self.tiles_dir, '16')
            if not os.path.exists(zoom16_path):
                return None, None
            
            x_dirs = os.listdir(zoom16_path)
            if not x_dirs:
                return None, None
            
            # İlk tile'ın koordinatını al
            first_x = int(x_dirs[0])
            first_x_path = os.path.join(zoom16_path, x_dirs[0])
            y_files = os.listdir(first_x_path)
            
            if not y_files:
                return None, None
            
            first_y = int(y_files[0].replace('.png', ''))
            
            # Tile koordinatından gerçek koordinata çevir
            import math
            zoom = 16
            n = 2.0 ** zoom
            lon_deg = first_x / n * 360.0 - 180.0
            lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * first_y / n)))
            lat_deg = math.degrees(lat_rad)
            
            print(f"İndirilen tile'dan hesaplanan koordinat: {lat_deg}, {lon_deg}")
            return lat_deg, lon_deg
            
        except Exception as e:
            print(f"Koordinat hesaplama hatası: {e}")
            return None, None

class MapHandler:
    def __init__(self, web_view: QWebEngineView, main_window):
        self.web_view = web_view
        self.main_window = main_window
        self.map_initialized = False
        self.waypoints = []
        self.flight_route = []
        self.is_waypoint_creation_active = False
        self.restricted_areas = []
        self.enemy_drones = []
        
        # Offline manager
        self.offline_manager = OfflineManager()
        
        # Web channel setup
        self.web_channel = QWebChannel()
        self.event_handler = MapEventHandler()
        self.web_channel.registerObject("pyObj", self.event_handler)
        self.web_view.page().setWebChannel(self.web_channel)

        # Event handler sinyallerine bağlan
        self.event_handler.coordinates_received.connect(self.handle_map_click)
        self.event_handler.right_click_received.connect(self.handle_right_click)

        # Varsayılan koordinatlarla başlat
        self.update_map(37.951, 32.500)

    def get_base64_icon(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, 'uav2.svg')
        try:
            with open(file_path, 'r') as file:
                svg_data = file.read()
            return base64.b64encode(svg_data.encode()).decode()
        except FileNotFoundError:
            print(f"Error: 'uav2.svg' not found in {base_dir}")
            return ""

    def get_base64_waypoint_icon(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, 'waypoint.svg')
        try:
            with open(file_path, 'r') as file:
                svg_data = file.read()
            return base64.b64encode(svg_data.encode()).decode()
        except FileNotFoundError:
            print(f"Error: 'waypoint.svg' not found in {base_dir}")
            return ""

    def get_base64_enemy_icon(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, 'enemy_drone.svg')
        try:
            with open(file_path, 'r') as file:
                svg_data = file.read()
            return base64.b64encode(svg_data.encode()).decode()
        except FileNotFoundError:
            print(f"Error: 'enemy_drone.svg' not found in {base_dir}")
            return ""

    def get_tile_url_template(self):
        """Tile URL şablonunu döndür (online/offline)"""
        # Önce offline tile'ların varlığını kontrol et
        has_offline = self.offline_manager.has_offline_tiles()
        has_internet = self.offline_manager.is_internet_available()
        
        if has_offline and not has_internet:
            # Offline mod - yerel HTTP server kullan
            print("Offline mode: Local HTTP server")
            self.offline_manager.start_tile_server()
            return f'http://localhost:{self.offline_manager.server_port}/{{z}}/{{x}}/{{y}}.png'
        elif has_internet:
            # Online mod - internet var
            print("Online mode: ArcGIS tiles")
            return 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
        else:
            # Ne offline tile var ne internet - fallback HTTP server
            print("Fallback mode: Local HTTP server (may not exist)")
            self.offline_manager.start_tile_server()
            return f'http://localhost:{self.offline_manager.server_port}/{{z}}/{{x}}/{{y}}.png'

    def get_available_tile_center(self):
        """Mevcut offline tile'lardan merkez koordinat bul"""
        try:
            # Zoom 16'dan başla (en detaylı)
            zoom16_path = os.path.join(self.offline_manager.tiles_dir, '16')
            if os.path.exists(zoom16_path):
                x_dirs = [d for d in os.listdir(zoom16_path) if os.path.isdir(os.path.join(zoom16_path, d))]
                if x_dirs:
                    # İlk x klasöründen bir tile al
                    first_x = int(x_dirs[0])
                    first_x_path = os.path.join(zoom16_path, x_dirs[0])
                    y_files = [f for f in os.listdir(first_x_path) if f.endswith('.png')]
                    
                    if y_files:
                        first_y = int(y_files[0].replace('.png', ''))
                        
                        # Tile koordinatından lat/lon'a çevir
                        import math
                        zoom = 16
                        n = 2.0 ** zoom
                        lon_deg = first_x / n * 360.0 - 180.0
                        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * first_y / n)))
                        lat_deg = math.degrees(lat_rad)
                        
                        print(f"Offline tile merkezi bulundu: {lat_deg}, {lon_deg}")
                        return lat_deg, lon_deg
        except Exception as e:
            print(f"Offline tile merkezi bulunamadı: {e}")
        
        # Varsayılan koordinatlar
        return 37.951, 32.500

    def update_map(self, latitude, longitude):
        # Leaflet içeriğini al
        js_content, css_content, is_local = self.offline_manager.get_leaflet_content()
        tile_url = self.get_tile_url_template()
        
        # Eğer offline tile'lar varsa, onların merkez koordinatını kullan
        if not self.offline_manager.is_internet_available() and self.offline_manager.has_offline_tiles():
            center_lat, center_lon = self.get_available_tile_center()
            latitude, longitude = center_lat, center_lon
        
        print(f"Tile URL template: {tile_url}")
        print(f"Harita merkezi: {latitude}, {longitude}")
        
        marker_script = f"""
        console.log('Harita başlatılıyor...');
        console.log('Tile URL: {tile_url}');
        
        var map = L.map('map').setView([{latitude}, {longitude}], 16);
    
        // Uydu görünümü katmanı (dinamik URL)
        var satelliteLayer = L.tileLayer('{tile_url}', {{
            attribution: 'Tiles &copy; Esri',
            maxZoom: 18,
            minZoom: 14,
            errorTileUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAYAAABccqhmAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAAN1wAADdcBQiibeAAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAANbSURBVHic7doxAQAACMOwgX+TaWfBDyZAXOvdAeBNAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUgTAEIUwDCFIAwBSBMAQhTAMIUgDAFIEwBCFMAwhSAMAUg7AcAAAD//2Q9CQMAAAAZdEVYdENvbW1lbnQAQ3JlYXRlZCB3aXRoIEdJTVBkLmUHAAAAAElFTkSuQmCC'  // Boş gri tile
        }});
    
        satelliteLayer.addTo(map);
        console.log('Satellite layer eklendi');
    
        window.map = map;
    
        // Test için bir tile yüklenip yüklenmediğini kontrol et
        satelliteLayer.on('tileload', function(e) {{
            console.log('Tile yüklendi: ' + e.url);
        }});
        
        satelliteLayer.on('tileerror', function(e) {{
            console.log('Tile hatası: ' + e.tile.src);
        }});
    
        // Sağ tıklama olayını dinle
        map.on('contextmenu', function(e) {{
            var coord = e.latlng;
            var lat = coord.lat;
            var lng = coord.lng;
            pyObj.rightClickReceived(lat, lng);
        }});
    
        // Tıklama olayını dinle
        map.on('click', function(e) {{
            var coord = e.latlng;
            var lat = coord.lat;
            var lng = coord.lng;
            pyObj.coordinatesClicked(lat, lng);
        }});
    
        // Önceki rotaları temizle
        if (window.waypointLayer) {{
            map.removeLayer(window.waypointLayer);
        }}
        if (window.flightRouteLayer) {{
            map.removeLayer(window.flightRouteLayer);
        }}
        
        console.log('Harita hazır!');
        """

        if not self.map_initialized:
            # HTML oluştur
            if is_local:
                # Yerel içerik - doğrudan HTML'e embed et
                map_html = f"""
                <html>
                <head>
                    <title>Offline Map</title>
                    <style>{css_content}</style>
                    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
                </head>
                <body>
                    <div id="map" style="width: 100%; height: 100%;"></div>
                    <script>{js_content}</script>
                    <script>
                        new QWebChannel(qt.webChannelTransport, function(channel) {{
                            window.pyObj = channel.objects.pyObj;
                            // Leaflet yüklendikten sonra haritayı başlat
                            {marker_script}
                        }});
                    </script>
                </body>
                </html>
                """
            else:
                # CDN - normal script/link tag'leri
                map_html = f"""
                <html>
                <head>
                    <title>Offline Map</title>
                    <script src="{js_content}"></script>
                    <link rel="stylesheet" href="{css_content}"/>
                    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
                </head>
                <body>
                    <div id="map" style="width: 100%; height: 100%;"></div>
                    <script>
                        new QWebChannel(qt.webChannelTransport, function(channel) {{
                            window.pyObj = channel.objects.pyObj;
                            // CDN yüklenme süresini bekle
                            setTimeout(function() {{
                                {marker_script}
                            }}, 500);
                        }});
                    </script>
                </body>
                </html>
                """
                
            self.web_view.setHtml(map_html)
            self.map_initialized = True
        else:
            self.web_view.page().runJavaScript(marker_script)

    def handle_map_click(self, latitude, longitude):
        if self.is_waypoint_creation_active:
            print(f"Waypoint Eklendi: Enlem: {latitude}, Boylam: {longitude}")
            self.waypoints.append([latitude, longitude])
            self.update_waypoints()
            self.update_last_waypoint_marker(latitude, longitude)

    def handle_right_click(self, latitude, longitude):
        print(f"Sağ Tıklanan Koordinatlar: Enlem: {latitude}, Boylam: {longitude}")
        self.show_context_menu(latitude, longitude)

    def show_context_menu(self, latitude, longitude):
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        download_area_action = menu.addAction("Bu Bölgeyi İndir (800m)")
        start_waypoint_action = menu.addAction("Waypoint Oluştur")
        stop_waypoint_action = menu.addAction("Waypoint Oluşturmayı Bitir")
        save_waypoints_action = menu.addAction("Waypoint'leri Kaydet")
        clear_waypoints_action = menu.addAction("Waypointleri Temizle")
        clear_route_action = menu.addAction("Rota İzlerini Temizle")

        action = menu.exec(self.web_view.mapToGlobal(self.web_view.pos()))

        if action == download_area_action:
            self.download_area(latitude, longitude)
        elif action == start_waypoint_action:
            self.is_waypoint_creation_active = True
            self.waypoints = []
            print("Waypoint oluşturma modu aktif.")
        elif action == stop_waypoint_action:
            self.is_waypoint_creation_active = False
            print("Waypoint oluşturma modu pasif.")
        elif action == save_waypoints_action:
            self.save_waypoints_to_file()
            print("Waypoint'ler kaydedildi.")
        elif action == clear_waypoints_action:
            self.clear_waypoints()
            print("Tüm waypointler temizlendi.")
        elif action == clear_route_action:
            self.clear_flight_route()
            print("Rota izleri temizlendi.")

    def download_area(self, latitude, longitude):
        """Belirli bölgeyi indir"""
        if not self.offline_manager.is_internet_available():
            print("İnternet bağlantısı yok! İndirme yapılamıyor.")
            return
        
        print(f"Bölge indiriliyor: {latitude}, {longitude} (800m yarıçap)")
        
        # Progress bar göster
        self.main_window.show_progress_bar()
        
        # Tile downloader'ı başlat (800m yarıçap)
        self.tile_downloader = TileDownloader(latitude, longitude, 800)
        self.tile_downloader.progress_updated.connect(self.main_window.update_progress)
        self.tile_downloader.download_finished.connect(self.download_completed)
        self.tile_downloader.start()

    def download_completed(self, message):
        """İndirme tamamlandığında"""
        print(message)
        self.main_window.hide_progress_bar()
        
        # Haritayı yenile (offline tile'ları kullanmaya başlamak için)
        self.map_initialized = False
        center_lat, center_lon = self.get_available_tile_center()
        self.update_map(center_lat, center_lon)

    def clear_waypoints(self):
        self.waypoints.clear()
        clear_waypoints_script = """
        if (window.waypointLayer) {
            window.map.removeLayer(window.waypointLayer);
            window.waypointLayer = null;
        }
        if (window.waypointMarker) {
            window.map.removeLayer(window.waypointMarker);
            window.waypointMarker = null;
        }
        if (window.waypointNumbers && window.waypointNumbers.length > 0) {
            window.waypointNumbers.forEach(function(marker) {
                window.map.removeLayer(marker);
            });
            window.waypointNumbers = [];
        }
        window.map.eachLayer(function(layer) {
            if (layer instanceof L.Marker && layer.options.icon instanceof L.DivIcon) {
                var html = layer.options.icon.options.html;
                if (html && html.includes('FFA500')) {
                    window.map.removeLayer(layer);
                }
            }
        });
        console.log('Tüm waypointler ve numaraları temizlendi.');
        """
        self.web_view.page().runJavaScript(clear_waypoints_script)

    def clear_flight_route(self):
        self.flight_route.clear()
        clear_route_script = """
        if (window.flightRouteLayer) {
            window.map.removeLayer(window.flightRouteLayer);
            window.flightRouteLayer = null;
        }
        console.log('Uçuş rota izleri temizlendi.');
        """
        self.web_view.page().runJavaScript(clear_route_script)

    def save_waypoints_to_file(self):
        if not self.waypoints:
            print("Kaydedilecek waypoint bulunamadı.")
            return

        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, 'waypoints.txt')

        with open(file_path, 'w') as file:
            for point in self.waypoints:
                file.write(f"{point[0]}, {point[1]}\n")

        print(f"Waypoint'ler '{file_path}' dosyasına kaydedildi.")

    def update_waypoints(self):
        if len(self.waypoints) < 2:
            return
        waypoint_script = f"""
        if (window.waypointLayer) {{
            window.map.removeLayer(window.waypointLayer);
        }}
        window.waypointLayer = L.polyline({self.waypoints}, {{
            color: '#FFD700',
            weight: 4,
            opacity: 0.8,
            dashArray: '10, 5'
        }}).addTo(window.map);
        """
        self.web_view.page().runJavaScript(waypoint_script)

    def update_last_waypoint_marker(self, latitude, longitude):
        waypoint_icon = f"data:image/svg+xml;base64,{self.get_base64_waypoint_icon()}"
        waypoint_number = len(self.waypoints)
        
        last_waypoint_script = f"""
        if (window.waypointMarker) {{
            window.map.removeLayer(window.waypointMarker);
        }}
        if (!window.waypointNumbers) {{
            window.waypointNumbers = [];
        }}
        window.waypointMarker = L.marker([{latitude}, {longitude}], {{
            icon: L.icon({{
                iconUrl: '{waypoint_icon}',
                iconSize: [25, 25]
            }})
        }}).addTo(window.map);
        var numberMarker = L.marker([{latitude}, {longitude}], {{
            icon: L.divIcon({{
                html: '<div style="background: #FFA500; color: white; border-radius: 50%; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 12px; border: 2px solid white;">{waypoint_number}</div>',
                className: '',
                iconSize: [20, 20],
                iconAnchor: [10, 10]
            }})
        }}).addTo(window.map);
        window.waypointNumbers.push(numberMarker);
        """
        self.web_view.page().runJavaScript(last_waypoint_script)

    def update_marker(self, latitude, longitude, yaw):
        if self.map_initialized:
            self.flight_route.append([latitude, longitude])
            self.update_flight_route()
            self.update_last_flight_marker(latitude, longitude, yaw)
        else:
            self.update_map(37.951560201667846, 32.50058144330979)
            self.map_initialized = True

    def update_flight_route(self):
        if len(self.flight_route) < 2:
            return
        flight_route_script = f"""
        if (window.flightRouteLayer) {{
            window.map.removeLayer(window.flightRouteLayer);
        }}
        window.flightRouteLayer = L.polyline({self.flight_route}, {{
            color: '#32CD32',
            weight: 3,
            opacity: 1.0
        }}).addTo(window.map);
        """
        self.web_view.page().runJavaScript(flight_route_script)

    def update_last_flight_marker(self, latitude, longitude, yaw):
        uav_icon = f"data:image/svg+xml;base64,{self.get_base64_icon()}"
        last_flight_script = f"""
        if (window.flightMarker) {{
            window.map.removeLayer(window.flightMarker);
        }}
        window.flightMarker = L.marker([{latitude}, {longitude}], {{
            icon: L.divIcon({{
                html: `<div style="transform: rotate({yaw}deg); filter: hue-rotate(200deg);"><img src='{uav_icon}' style='width: 25px; height: 25px;'></div>`,
                className: '',
                iconSize: [25, 25],
                iconAnchor: [12.5, 12.5]
            }})
        }}).addTo(window.map);
        """
        self.web_view.page().runJavaScript(last_flight_script)

    def update_restricted_area_marker(self, latitude, longitude, radius):
        restricted_area_script = f"""
        var circle = L.circle([{latitude}, {longitude}], {{
            color: '#FF0000',
            fillColor: '#FF0000',
            fillOpacity: 0.15,
            weight: 3,
            radius: {radius}
        }}).addTo(window.map);
        """
        self.web_view.page().runJavaScript(restricted_area_script)

    def update_enemy_drone_marker(self, latitude, longitude):
        enemy_drone_icon = f"data:image/svg+xml;base64,{self.get_base64_enemy_icon()}"
        enemy_drone_script = f"""
        var marker = L.marker([{latitude}, {longitude}], {{
            icon: L.icon({{
                iconUrl: '{enemy_drone_icon}',
                iconSize: [25, 25]
            }})
        }}).addTo(window.map);
        """
        self.web_view.page().runJavaScript(enemy_drone_script)

    def update_flight_area_marker(self, coordinates):
        flight_area_script = f"""
        var latlngs = {coordinates};
        var polygon = L.polygon(latlngs, {{
            color: '#00AA00',
            fillColor: '#00FF00',
            fillOpacity: 0.1,
            weight: 2
        }}).addTo(window.map);
        """
        self.web_view.page().runJavaScript(flight_area_script)


class MapEventHandler(QObject):
    coordinates_received = Signal(float, float)
    right_click_received = Signal(float, float)

    @Slot(float, float)
    def coordinatesClicked(self, latitude, longitude):
        self.coordinates_received.emit(latitude, longitude)

    @Slot(float, float)
    def rightClickReceived(self, latitude, longitude):
        self.right_click_received.emit(latitude, longitude)


class MapWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Offline Map Application")
        self.setGeometry(100, 100, 800, 600)

        # Ana widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Layout
        layout = QVBoxLayout(central_widget)
        
        # Progress bar (gizli)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Web view
        self.web_view = QWebEngineView(self)
        
        # QWebEngineView için yerel dosya erişimini etkinleştir
        settings = self.web_view.settings()
        settings.setAttribute(settings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(settings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(settings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(settings.WebAttribute.AllowRunningInsecureContent, True)
        
        layout.addWidget(self.web_view)

        # Initialize MapHandler
        self.map_handler = MapHandler(self.web_view, self)

    def closeEvent(self, event):
        """Uygulama kapatılırken tile server'ını durdur"""
        if self.map_handler.offline_manager.tile_server:
            print("Tile server durduruluyor...")
            self.map_handler.offline_manager.stop_tile_server()
        event.accept()

    def show_progress_bar(self):
        """Progress bar'ı göster"""
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

    def hide_progress_bar(self):
        """Progress bar'ı gizle"""
        self.progress_bar.setVisible(False)

    def update_progress(self, current, total):
        """Progress bar'ı güncelle"""
        if total > 0:
            progress = int((current / total) * 100)
            self.progress_bar.setValue(progress)
            self.progress_bar.setFormat(f"İndiriliyor: {current}/{total} tile (%p%)")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MapWindow()
    window.show()
    sys.exit(app.exec())
