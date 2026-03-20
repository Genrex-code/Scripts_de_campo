#crear un sistema de QUEUES con un limite de tamaño
# en caso de que el sistema intente colapsar por si registra
#señales por encima de las capacidades 
import re

class RadioLogic:
    def __init__(self):
        # Patrones para identificar lo que SÍ nos importa del Snapdragon
        self.patterns = {
            "signal": re.compile(r"SignalStrength:.*?dbm=(-?\+?\d+)"),
            "cipher": re.compile(r"ciphering=(?P<state>\d)"),
            "cell_id": re.compile(r"cellIdentityCellid=(?P<id>\d+)"),
            "operator": re.compile(r"operator=(?P<name>.*?),")
        }

    def clean_batch(self, batch):
        cleaned_data = []
        for entry in batch:
            payload = entry['raw_payload']
            # Aquí aplicamos la lógica de "investigador"
            # Si la línea contiene info de señal o seguridad, la extraemos
            for key, pattern in self.patterns.items():
                match = pattern.search(payload)
                if match:
                    entry[f"extracted_{key}"] = match.group(1) if key == "signal" else match.group('state' if key == "cipher" else 'id' if key == "cell_id" else 'name')
            cleaned_data.append(entry)
        return cleaned_data