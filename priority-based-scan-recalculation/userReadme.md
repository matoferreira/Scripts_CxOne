# Recálculo de escaneos en Checkmarx One

Script para ejecutar manualmente recálculos de escaneos (`POST /api/scans/recalculate`) según la prioridad de cada proyecto definida en un archivo Excel.

## ¿Qué hace este script?

1. Lee la lista de proyectos desde `pr_codigo.xlsx`.
2. Busca cada proyecto en Checkmarx One por **nombre exacto**.
3. Obtiene la fecha del **último escaneo completado**.
4. Compara esa fecha con el umbral de días según la **prioridad** del proyecto.
5. Si corresponde, dispara un **recálculo** usando los mismos motores (`engines`) y la misma rama (`branch`) del último escaneo.
6. Genera un **informe en CSV y Excel** como evidencia de la ejecución.

## Requisitos

- Python 3.10 o superior
- Acceso a Checkmarx One con una API Key válida
- Archivo de entrada `pr_codigo.xlsx`

## Instalación

```bash
pip install -r requirements.txt
```

## Configuración

Copie `.env.example` a `.env` y complete los valores:

```bash
cp .env.example .env
```

### Credenciales y región

| Variable | Descripción |
|----------|-------------|
| `CHECKMARX_TENANT` | Nombre del tenant en IAM (debe coincidir exactamente con el de su API Key) |
| `CHECKMARX_BASE_URL` | URL base del entorno AST |
| `CHECKMARX_AUTH_URL` | URL base de autenticación IAM |
| `CHECKMARX_API_KEY` | API Key generada en Checkmarx One → User Management → API Keys |

**Regiones disponibles:**

| Región | `CHECKMARX_BASE_URL` | `CHECKMARX_AUTH_URL` |
|--------|----------------------|----------------------|
| US1 | `https://ast.checkmarx.net` | `https://iam.checkmarx.net` |
| US2 | `https://us.ast.checkmarx.net` | `https://us.iam.checkmarx.net` |

Documentación de autenticación: [Checkmarx One API – Authentication](https://checkmarx.stoplight.io/docs/checkmarx-one-api-reference-guide/ybti0d5u0auto-authentication)

### Días por prioridad

Puede ajustar cuántos días deben pasar desde el último escaneo completado antes de recalcular. Edite estas variables en `.env`:

```env
PRIORITY_1_DAYS=7    # Prioridad 1: recalcular si pasaron más de 7 días
PRIORITY_2_DAYS=15   # Prioridad 2: recalcular si pasaron más de 15 días
PRIORITY_3_DAYS=30   # Prioridad 3: recalcular si pasaron más de 30 días
```

No es necesario modificar el código: al cambiar estos valores y volver a ejecutar el script, se aplican los nuevos umbrales. Los valores también quedan registrados en la hoja **Summary** del informe Excel.

## Archivo de entrada (`pr_codigo.xlsx`)

El Excel debe tener dos columnas en la primera hoja:

| Project name | Priority |
|--------------|----------|
| organizacion/mi-proyecto | 1 |
| organizacion/otro-proyecto | 2 |

**Importante:**

- El nombre del proyecto debe coincidir **exactamente** con el nombre en Checkmarx One.
- La prioridad debe ser `1`, `2` o `3`.
- Los espacios al inicio o al final del nombre se eliminan automáticamente.

## Ejecución

### Simulación (sin disparar recálculos)

Recomendado antes de la primera ejecución o después de cambiar la configuración:

```bash
python checkmarx_rescans.py --dry-run
```

### Ejecución real

```bash
python checkmarx_rescans.py
```

### Opciones adicionales

```bash
# Usar otro archivo Excel
python checkmarx_rescans.py --excel ruta/al/archivo.xlsx

# Guardar informes en otra carpeta
python checkmarx_rescans.py --report-dir ruta/a/informes
```

## Criterios de decisión

| Situación | Acción |
|-----------|--------|
| No existe el proyecto en Checkmarx One | Se omite |
| No hay escaneos completados previos | Se omite |
| El último escaneo completado está dentro del umbral | Se omite |
| El último escaneo completado superó el umbral | Se dispara recálculo |
| Error de API | Se registra en el informe |

El recálculo reutiliza los motores y la rama del último escaneo completado.

## Informes de salida

Cada ejecución genera dos archivos en la carpeta `reports/`:

```
reports/rescans_report_AAAA-MM-DD_HHMMSS.csv
reports/rescans_report_AAAA-MM-DD_HHMMSS.xlsx
```

### Hoja Summary (Excel)

Resumen de la ejecución: fecha, tenant, URL de Checkmarx, archivo de entrada, umbrales de días, totales (disparados / omitidos / errores).

### Hoja Results (Excel) / CSV

Una fila por proyecto con:

- Fecha de ejecución (UTC)
- Nombre y prioridad del proyecto
- Umbral de días aplicado
- Estado: `TRIGGERED`, `SKIPPED`, `DRY RUN` o `ERROR`
- Motivo de la decisión
- ID del proyecto en Checkmarx
- Fecha del último escaneo y días transcurridos
- Rama y motores utilizados
- ID del nuevo escaneo (si se disparó recálculo)
- Detalle del error (si hubo fallo)

Estos archivos sirven como evidencia para auditoría o entrega al cliente.

## Solución de problemas

### `Realm does not exist`

El valor de `CHECKMARX_TENANT` no coincide con el tenant de su API Key. Verifique el nombre exacto en IAM (por ejemplo, guiones y guiones bajos importan).

### `Project not found`

El nombre en el Excel no coincide exactamente con el de Checkmarx One. Revise mayúsculas, barras (`/`) y espacios.

### `Authentication failed`

- Confirme que la API Key no haya expirado.
- Verifique que `CHECKMARX_AUTH_URL` corresponda a la región correcta (US1 vs US2).

## Estructura del proyecto

```
.
├── checkmarx_rescans.py   # Script principal
├── pr_codigo.xlsx         # Lista de proyectos y prioridades
├── .env                   # Configuración local (no versionar)
├── .env.example           # Plantilla de configuración
├── requirements.txt       # Dependencias Python
├── README.md              # Guía para agentes de IA
├── userReadme.md          # Esta guía para operadores
└── reports/               # Informes generados (no versionar)
```
