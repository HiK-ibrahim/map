import sys
import base64
import os
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWebChannel import QWebChannel


class MapHandler:
    def __init__(self, web_view: QWebEngineView):
        self.web_view = web_view
        self.map_initialized = False
        self.waypoints = []  # Waypoint noktalarını saklamak için liste
        self.flight_route = []  # Uçuş rotasını saklamak için liste
        self.is_waypoint_creation_active = False  # Waypoint oluşturma modu aktif mi?
        self.restricted_areas = []
        self.enemy_drones = []  # Düşman İHA'lar
        self.web_channel = QWebChannel()
        self.event_handler = MapEventHandler()  # Harita olaylarını işlemek için handler
        self.web_channel.registerObject("pyObj", self.event_handler)
        self.web_view.page().setWebChannel(self.web_channel)

        # Event handler sinyallerine bağlan
        self.event_handler.coordinates_received.connect(self.handle_map_click)
        self.event_handler.right_click_received.connect(self.handle_right_click)

        self.update_map(0, 0)  # Başlangıçta haritayı yükle

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

    def update_map(self, latitude, longitude):
        marker_script = f"""
        var map = L.map('map').setView([{latitude}, {longitude}], 19);
    
        // Harita katmanları
        var osmLayer = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            maxZoom: 19
        }});
    
        var satelliteLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
            attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics',
            maxZoom: 19
        }});
    
        satelliteLayer.addTo(map);  // Başlangıçta uydu görünümü
    
        var baseMaps = {{
            "Harita Görünümü": osmLayer,
            "Uydu Görünümü": satelliteLayer
        }};
    
        L.control.layers(baseMaps).addTo(map);
    
        window.map = map;  // Haritayı global olarak sakla
    
        // Sağ tıklama olayını dinle
        map.on('contextmenu', function(e) {{
            var coord = e.latlng;
            var lat = coord.lat;
            var lng = coord.lng;
            pyObj.rightClickReceived(lat, lng);  // Python tarafına gönder
        }});
    
        // Tıklama olayını dinle
        map.on('click', function(e) {{
            var coord = e.latlng;
            var lat = coord.lat;
            var lng = coord.lng;
            pyObj.coordinatesClicked(lat, lng);  // Python tarafına gönder
        }});
    
        // Önceki rotaları temizle
        if (window.waypointLayer) {{
            map.removeLayer(window.waypointLayer);
        }}
        if (window.flightRouteLayer) {{
            map.removeLayer(window.flightRouteLayer);
        }}
        """

        if not self.map_initialized:
            map_html = f"""
            <html>
            <head>
                <title>Map</title>
                <script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
                <link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css"/>
                <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            </head>
            <body>
                <div id="map" style="width: 100%; height: 100%;"></div>
                <script>
                    new QWebChannel(qt.webChannelTransport, function(channel) {{
                        window.pyObj = channel.objects.pyObj;
                    }});
                    {marker_script}
                </script>
            </body>
            </html>
            """
            self.web_view.setHtml(map_html)
            self.map_initialized = True
        else:
            self.web_view.page().runJavaScript(marker_script)

    def handle_map_click(self, latitude, longitude):
        """
        Haritada tıklanan koordinatları işleyen fonksiyon.
        """
        if self.is_waypoint_creation_active:
            print(f"Waypoint Eklendi: Enlem: {latitude}, Boylam: {longitude}")
            self.waypoints.append([latitude, longitude])  # Yeni waypoint ekle
            self.update_waypoints()  # Waypoint rotasını güncelle
            self.update_last_waypoint_marker(latitude, longitude)  # Son waypoint işaretçisini güncelle

    def handle_right_click(self, latitude, longitude):
        """
        Sağ tıklama olayını işleyen fonksiyon.
        """
        print(f"Sağ Tıklanan Koordinatlar: Enlem: {latitude}, Boylam: {longitude}")
        self.show_context_menu(latitude, longitude)

    def show_context_menu(self, latitude, longitude):
        """
        Sağ tıklama menüsünü gösterir.
        """
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        start_waypoint_action = menu.addAction("Waypoint Oluştur")
        stop_waypoint_action = menu.addAction("Waypoint Oluşturmayı Bitir")
        save_waypoints_action = menu.addAction("Waypoint'leri Kaydet")
        clear_waypoints_action = menu.addAction("Waypointleri Temizle")
        clear_route_action = menu.addAction("Rota İzlerini Temizle")

        # Menüyü harita üzerindeki fare pozisyonunda göster
        action = menu.exec_(self.web_view.mapToGlobal(self.web_view.pos()))

        if action == start_waypoint_action:
            self.is_waypoint_creation_active = True
            self.waypoints = []  # Yeni waypoint listesi için temizle
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

    def clear_waypoints(self):
        """
        Haritadaki tüm waypointleri ve numaralarını temizler.
        """
        self.waypoints.clear()  # Waypoint listesini temizle

        # JavaScript kodu ile haritadaki waypointleri ve numaraları tamamen temizle
        clear_waypoints_script = """
        // Waypoint rotasını temizle
        if (window.waypointLayer) {
            window.map.removeLayer(window.waypointLayer);
            window.waypointLayer = null;
        }
        
        // Waypoint işaretçilerini temizle
        if (window.waypointMarker) {
            window.map.removeLayer(window.waypointMarker);
            window.waypointMarker = null;
        }
        
        // Tüm waypoint numaralarını temizle (eğer array olarak saklanıyorsa)
        if (window.waypointNumbers && window.waypointNumbers.length > 0) {
            window.waypointNumbers.forEach(function(marker) {
                window.map.removeLayer(marker);
            });
            window.waypointNumbers = [];
        }
        
        // Tüm marker'ları gözden geçir ve waypoint ile ilgili olanları temizle
        window.map.eachLayer(function(layer) {
            // DivIcon kullanan waypoint numaralarını tespit et ve sil
            if (layer instanceof L.Marker && layer.options.icon instanceof L.DivIcon) {
                var html = layer.options.icon.options.html;
                if (html && html.includes('FFA500')) { // Turuncu waypoint numarası rengi
                    window.map.removeLayer(layer);
                }
            }
        });
        
        console.log('Tüm waypointler ve numaraları temizlendi.');
        """
        self.web_view.page().runJavaScript(clear_waypoints_script)

    def clear_flight_route(self):
        """
        Haritadaki tüm uçuş rotalarını (yeşil izleri) temizler.
        """
        self.flight_route.clear()  # Uçuş rotası listesini temizle

        # JavaScript kodu ile haritadaki uçuş rotalarını temizle
        clear_route_script = """
        // Uçuş rotasını temizle (yeşil çizgiler)
        if (window.flightRouteLayer) {
            window.map.removeLayer(window.flightRouteLayer);
            window.flightRouteLayer = null;
        }
        
        // Drone işaretçisini koru ama rota çizgilerini sil
        console.log('Uçuş rota izleri temizlendi.');
        """
        self.web_view.page().runJavaScript(clear_route_script)

    def save_waypoints_to_file(self):
        """
        Waypoint'leri bir txt dosyasına kaydeder.
        """
        if not self.waypoints:
            print("Kaydedilecek waypoint bulunamadı.")
            return

        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, 'waypoints.txt')

        with open(file_path, 'w') as file:
            for point in self.waypoints:
                file.write(f"{point[0]}, {point[1]}\n")

        print(f"Waypoint'ler '{file_path}' dosyasına kaydedildi.")

    def update_restricted_area_marker(self, latitude, longitude, radius):
        """
        Yasaklı alan işaretçisini günceller - KIRMIZI
        """
        restricted_area_script = f"""
        var circle = L.circle([{latitude}, {longitude}], {{
            color: '#FF0000',           // Kırmızı kenar
            fillColor: '#FF0000',       // Kırmızı dolgu
            fillOpacity: 0.15,          // %15 şeffaflık
            weight: 3,                  // Kenar kalınlığı
            radius: {radius}
        }}).addTo(window.map);
        """
        self.web_view.page().runJavaScript(restricted_area_script)

    def update_enemy_drone_marker(self, latitude, longitude):
        """
        Düşman drone işaretçisini günceller - KOYU KIRMIZI
        """
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

    def update_waypoints(self):
        """
        Harita üzerinde waypoint rotasını günceller - SARI/TURUNCU
        """
        if len(self.waypoints) < 2:
            return  # En az iki waypoint olmalı

        # Waypoint rotasını güncellemek için JavaScript kodu - SARI
        waypoint_script = f"""
        if (window.waypointLayer) {{
            window.map.removeLayer(window.waypointLayer);
        }}
        window.waypointLayer = L.polyline({self.waypoints}, {{
            color: '#FFD700',           // Altın sarısı
            weight: 4,                  // Kalın çizgi
            opacity: 0.8,               // Biraz şeffaf
            dashArray: '10, 5'          // Kesik çizgi
        }}).addTo(window.map);
        """
        self.web_view.page().runJavaScript(waypoint_script)

    def update_last_waypoint_marker(self, latitude, longitude):
        """
        Son eklenen waypoint işaretçisini günceller - TURUNCU
        """
        waypoint_icon = f"data:image/svg+xml;base64,{self.get_base64_waypoint_icon()}"
        waypoint_number = len(self.waypoints)  # Waypoint numarası
        
        last_waypoint_script = f"""
        if (window.waypointMarker) {{
            window.map.removeLayer(window.waypointMarker);
        }}
        
        // Waypoint numaralarını saklamak için array oluştur (yoksa)
        if (!window.waypointNumbers) {{
            window.waypointNumbers = [];
        }}
        
        // Waypoint işaretçisi
        window.waypointMarker = L.marker([{latitude}, {longitude}], {{
            icon: L.icon({{
                iconUrl: '{waypoint_icon}',
                iconSize: [25, 25]
            }})
        }}).addTo(window.map);
        
        // Waypoint numarası etiketi
        var numberMarker = L.marker([{latitude}, {longitude}], {{
            icon: L.divIcon({{
                html: '<div style="background: #FFA500; color: white; border-radius: 50%; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 12px; border: 2px solid white;">{waypoint_number}</div>',
                className: '',
                iconSize: [20, 20],
                iconAnchor: [10, 10]
            }})
        }}).addTo(window.map);
        
        // Numarayı array'e ekle (temizlerken silebilmek için)
        window.waypointNumbers.push(numberMarker);
        """
        self.web_view.page().runJavaScript(last_waypoint_script)

    def update_flight_route(self):
        """
        Harita üzerinde uçuş rotasını günceller - YEŞİL
        """
        if len(self.flight_route) < 2:
            return  # En az iki nokta olmalı

        # Uçuş rotasını güncellemek için JavaScript kodu - YEŞİL
        flight_route_script = f"""
        if (window.flightRouteLayer) {{
            window.map.removeLayer(window.flightRouteLayer);
        }}
        window.flightRouteLayer = L.polyline({self.flight_route}, {{
            color: '#32CD32',           // Canlı yeşil
            weight: 3,                  // Orta kalınlık
            opacity: 1.0               // Tam opak
        }}).addTo(window.map);
        """
        self.web_view.page().runJavaScript(flight_route_script)

    def update_flight_area_marker(self, coordinates):
        """
        Uçuş alanı işaretçisini günceller - YEŞİL ALAN
        """
        flight_area_script = f"""
        var latlngs = {coordinates};
        var polygon = L.polygon(latlngs, {{
            color: '#00AA00',           // Koyu yeşil kenar
            fillColor: '#00FF00',       // Açık yeşil dolgu
            fillOpacity: 0.1,           // %10 şeffaflık
            weight: 2
        }}).addTo(window.map);
        """
        self.web_view.page().runJavaScript(flight_area_script)

    def update_marker(self, latitude, longitude, yaw):
        """
        Haritaya yeni bir işaretçi ekler ve uçuş rotasını günceller.
        """
        if self.map_initialized:
            self.flight_route.append([latitude, longitude])  # Yeni koordinatı uçuş rotasına ekle
            self.update_flight_route()  # Uçuş rotasını güncelle
            self.update_last_flight_marker(latitude, longitude, yaw)  # Son uçuş işaretçisini güncelle
        else:
            self.update_map(latitude, longitude)
            self.map_initialized = True

    def update_last_flight_marker(self, latitude, longitude, yaw):
        """
        Son eklenen uçuş işaretçisini günceller ve yön açısını uygular - MAVİ DRONE
        """
        uav_icon = f"data:image/svg+xml;base64,{self.get_base64_icon()}"
        last_flight_script = f"""
        if (window.flightMarker) {{
            window.map.removeLayer(window.flightMarker);
        }}
        
        // Drone işaretçisi - mavi renkte
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


class MapEventHandler(QObject):
    coordinates_received = Signal(float, float)  # Koordinatları yaymak için sinyal
    right_click_received = Signal(float, float)  # Sağ tıklama sinyali

    @Slot(float, float)
    def coordinatesClicked(self, latitude, longitude):
        self.coordinates_received.emit(latitude, longitude)  # Sinyal yay

    @Slot(float, float)
    def rightClickReceived(self, latitude, longitude):
        self.right_click_received.emit(latitude, longitude)  # Sağ tıklama sinyali yay


class MapWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Map Application")
        self.setGeometry(100, 100, 800, 600)

        # Create QWebEngineView
        self.web_view = QWebEngineView(self)
        self.setCentralWidget(self.web_view)

        # Initialize MapHandler
        self.map_handler = MapHandler(self.web_view)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MapWindow()
    window.show()
    sys.exit(app.exec())
