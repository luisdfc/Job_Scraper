# Ranking contextual y validación

## Qué se interpreta

El texto se separa en funciones, requisitos, preferencias, presentación de empresa, beneficios y metadatos. Se unen fragmentos cortados al extraer HTML. La descripción original siempre se conserva.

Las funciones aportan afinidad. La formación pedida no demuestra que la tarea use econometría. Un seguro médico o una plataforma de formación con IA no acreditan trabajo financiero o de IA. La empresa puede confirmar el sector, pero sus actividades no se atribuyen automáticamente al puesto.

Los bonos de una misma familia de puesto no se acumulan. Las reglas y los límites citan evidencia del anuncio. La configuración personal permanece en `private/ranking.json`; el código público no contiene el perfil del candidato.

## Requisitos

- Se usa el extremo inferior de los rangos de experiencia. Las preferencias explícitas no penalizan.
- Una diferencia aproximada de un año pesa poco; la experiencia y la categoría senior no se suman dos veces por el mismo motivo.
- La experiencia transferible no se convierte en experiencia profesional de inversión, econometría, producto o ingeniería. Se muestra una advertencia cuando el anuncio pide una especialización no acreditada.
- Se respetan las alternativas de formación económica o empresarial y de experiencia equivalente.
- No se asume programación avanzada por usar Python ni por coordinar un proyecto asistido con IA.
- La falta de salario o datos fiables no supone incompatibilidad. Los cierres no se infieren de bloqueos, errores o ausencia de botón.

Las puntuaciones están acotadas. Cuando la actividad central pertenece a otra profesión o la responsabilidad excede claramente el perfil, se aplica un techo de prioridad visible en la explicación. Las ofertas siguen siendo consultables.

## Ubicación, identidad y vigencia

El catálogo local contiene los 179 municipios del [conjunto oficial de la Comunidad de Madrid](https://datos.comunidad.madrid/dataset/municipio_comunidad_madrid), descargado el 19 de septiembre de 2026. Una ubicación no reconocida se mantiene pendiente de confirmar; no se interpreta automáticamente como fuera de Madrid.

Los enlaces equivalentes y las identidades ya conocidas se consolidan. Títulos parecidos con identificadores distintos se señalan como posibles duplicados y se conservan. Las señales de vigencia contradictorias pasan a «No verificable».

La comprobación de vigencia detiene peticiones al dominio que falla, sin bloquear otros empleadores de la misma fuente. Una acción de solicitud activa en una ficha coincidente es una señal de apertura en ese momento, no una garantía. Los finales ambiguos de LinkedIn guardan una muestra local y su huella para investigar el parser. No se reinterpretan como búsquedas completas.

## Calibración y límites

Las pruebas públicas usan ejemplos sintéticos. La calibración real es local: se revisan primeros resultados, falsos positivos y oportunidades inicialmente relegadas. Incluye puntuaciones orientativas y comparaciones de orden; no evalúa probabilidad de respuesta o contratación.

El parser de secciones y los patrones son heurísticos en español e inglés. Los anuncios ambiguos, mal estructurados o contradictorios pueden requerir revisión. El score no certifica todos los requisitos. Conviene ajustar la calibración con decisiones explícitas del usuario tras varias búsquedas, sin convertir automáticamente cada descarte en una preferencia general.

Las consultas mantienen su amplitud. El diagnóstico guarda aportación de ofertas únicas y candidatos con prioridad alta por consulta. Varias capturas permiten decidir qué consultas aportan oportunidades; una sola no justifica recortar cobertura.
