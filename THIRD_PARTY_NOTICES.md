# Third-Party Software Notices & Licenses

PeakShift incorporates or links against open-source software and font packages licensed under their respective terms.

---

## 1. Bundled Frontend Vendor Assets

### Plotly.js
- **Location**: `app/static/vendor/plotly.min.js`
- **License**: MIT License
- **Copyright**: Copyright (c) 2012-2024 Plotly, Inc.
- **Notice**:
```
The MIT License (MIT)
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.
```

### Inter Font
- **Location**: `app/static/fonts/Inter-*.woff2`
- **License**: SIL Open Font License (OFL), Version 1.1
- **Copyright**: Copyright (c) 2016-2024 The Inter Project Authors (https://github.com/rsms/inter)
- **Notice**:
```
This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is available with a FAQ at: http://scripts.sil.org/OFL
```

### JetBrains Mono Font
- **Location**: `app/static/fonts/JetBrainsMono-Regular.woff2`
- **License**: SIL Open Font License (OFL), Version 1.1
- **Copyright**: Copyright (c) 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)
- **Notice**:
```
This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is available with a FAQ at: http://scripts.sil.org/OFL
```

---

## 2. Python Dependencies

The following Python packages are utilized:

| Package | License | Primary Purpose |
|---|---|---|
| **FastAPI** | MIT License | High-performance ASGI REST API framework |
| **Starlette** | BSD-3-Clause | ASGI application toolkit and request routing |
| **Uvicorn** | BSD-3-Clause | Asynchronous ASGI server implementation |
| **Pydantic** | MIT License | Strict data validation and schema serialization |
| **Pandas** | BSD-3-Clause | Tabular data processing for CSV and Excel ingestion |
| **NumPy** | BSD-3-Clause | Numerical and mathematical utilities |
| **OpenPyXL** | MIT License | Native Excel workbook (.xlsx) reading and writing |
| **Pytest** | MIT License | Unit, contract, and property test execution |
| **HTTPX** | BSD-3-Clause | Fast HTTP client for API integration testing |
| **AnyIO** | MIT License | Asynchronous concurrency abstraction layer |
