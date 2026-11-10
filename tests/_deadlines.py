"""Prazos compartilhados da suíte (sprint 64).

``ANTI_HANG`` é a rede de segurança contra travamento de esperas POSITIVAS
(``assert evento.wait(...)``, ``future.result(timeout=...)``): no caminho feliz
ela nunca é alcançada; só decide quanto se espera antes de ver uma falha real.
Valores de 1 a 10s viraram falso vermelho no runner Windows (setup do worker
governado, SDK Agno e SQLite várias vezes mais lentos, 4 workers xdist). Ver
``DEADLINE_ANTI_TRAVAMENTO`` em ``conftest.py``. Esperas NEGATIVAS
(``assert not evento.wait(0.1)``) provam ausência e continuam curtas.
"""

ANTI_HANG = 30.0
