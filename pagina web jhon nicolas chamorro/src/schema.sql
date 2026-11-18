-- Esquema del sistema de control de almacén
-- Orden de borrado inverso a las dependencias

DROP TABLE IF EXISTS venta_detalle;
DROP TABLE IF EXISTS ventas;
DROP TABLE IF EXISTS pedido_detalle;
DROP TABLE IF EXISTS pedidos;
DROP TABLE IF EXISTS movimientos;
DROP TABLE IF EXISTS productos;
DROP TABLE IF EXISTS usuarios;
DROP TABLE IF EXISTS configuracion;

CREATE TABLE usuarios (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    nombre         VARCHAR(120) NOT NULL,
    email          VARCHAR(120) NOT NULL UNIQUE,
    password_hash  VARCHAR(255) NOT NULL,
    rol            ENUM('admin', 'trabajador', 'cliente') NOT NULL DEFAULT 'cliente',
    activo         TINYINT(1) NOT NULL DEFAULT 1,
    creado_en      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE productos (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    codigo             VARCHAR(40) NOT NULL UNIQUE,
    nombre             VARCHAR(150) NOT NULL,
    descripcion        TEXT,
    categoria          VARCHAR(80) NOT NULL DEFAULT 'General',
    precio             DECIMAL(12,2) NOT NULL DEFAULT 0,
    stock              INT NOT NULL DEFAULT 0,
    stock_minimo       INT NOT NULL DEFAULT 0,
    imagen             VARCHAR(255) NULL,
    activo             TINYINT(1) NOT NULL DEFAULT 1,
    creado_en          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ultimo_movimiento  DATETIME NULL,
    CHECK (stock >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Entradas y salidas de bodega (toda variación de stock queda aquí)
CREATE TABLE movimientos (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    producto_id  INT NOT NULL,
    tipo         ENUM('entrada', 'salida') NOT NULL,
    cantidad     INT NOT NULL,
    motivo       VARCHAR(255) NOT NULL DEFAULT '',
    pedido_id    INT NULL,
    usuario_id   INT NULL,
    fecha        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (producto_id) REFERENCES productos(id),
    FOREIGN KEY (usuario_id)  REFERENCES usuarios(id),
    INDEX idx_mov_fecha (fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE pedidos (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id      INT NOT NULL,
    estado          ENUM('pendiente', 'preparacion', 'despachado', 'cancelado') NOT NULL DEFAULT 'pendiente',
    total           DECIMAL(12,2) NOT NULL DEFAULT 0,
    notas           VARCHAR(500) NOT NULL DEFAULT '',
    atendido_por    INT NULL,
    creado_en       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (cliente_id)   REFERENCES usuarios(id),
    FOREIGN KEY (atendido_por) REFERENCES usuarios(id),
    INDEX idx_ped_estado (estado)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE pedido_detalle (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    pedido_id        INT NOT NULL,
    producto_id      INT NOT NULL,
    cantidad         INT NOT NULL,
    precio_unitario  DECIMAL(12,2) NOT NULL,
    FOREIGN KEY (pedido_id)   REFERENCES pedidos(id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id) REFERENCES productos(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Una venta se genera automáticamente al despachar un pedido
CREATE TABLE ventas (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    pedido_id   INT NULL UNIQUE,
    cliente_id  INT NOT NULL,
    usuario_id  INT NULL,
    total       DECIMAL(12,2) NOT NULL,
    fecha       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pedido_id)  REFERENCES pedidos(id) ON DELETE SET NULL,
    FOREIGN KEY (cliente_id) REFERENCES usuarios(id),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
    INDEX idx_venta_fecha (fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE venta_detalle (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    venta_id         INT NOT NULL,
    producto_id      INT NOT NULL,
    cantidad         INT NOT NULL,
    precio_unitario  DECIMAL(12,2) NOT NULL,
    FOREIGN KEY (venta_id)    REFERENCES ventas(id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id) REFERENCES productos(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE configuracion (
    clave  VARCHAR(60) PRIMARY KEY,
    valor  VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO configuracion (clave, valor) VALUES ('dias_estancado', '30');
