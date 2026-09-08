-- Logistics DDL - Draft v0.1
--
-- Conformance: ANSI SQL, intended to lint clean under
-- `sqlfluff lint --dialect ansi`.
-- Runs unmodified in DuckDB 0.10+.
--
-- Source ERD: schemas/logistics/logistics_er.dbml
--
-- NOT-NULL rules: never nullify a PK or INNER-JOIN FK.
-- INNER-JOIN FKs: shipments.order_id, shipments.carrier_id,
-- inventory.warehouse_id.
-- Nullable analytical fields: orders.warehouse_id, carriers.base_rate,
-- shipments.ship_date, shipments.delivery_date, shipments.shipping_cost.
--
-- Orphan exception: orders.warehouse_id deliberately has no SQL FK because
-- Logistics requires orphaned orders. Values may be valid warehouse IDs, NULL,
-- or non-existent warehouse IDs.
--
-- Many-to-many note: orders can use multiple carriers through shipments, and
-- carriers can ship many orders. shipments is the relationship fact table;
-- no separate junction table is required by the v3 Logistics scope.

CREATE TABLE carriers (
    carrier_id INTEGER NOT NULL,
    carrier_name VARCHAR(255) NOT NULL,
    service_level VARCHAR(32) NOT NULL,
    carrier_type VARCHAR(32),
    base_rate DECIMAL(10, 2),
    currency_code CHAR(3) NOT NULL DEFAULT 'USD',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_carriers PRIMARY KEY (carrier_id)
);

CREATE TABLE warehouses (
    warehouse_id INTEGER NOT NULL,
    warehouse_name VARCHAR(255) NOT NULL,
    region VARCHAR(32),
    country_code CHAR(2),
    capacity_units INTEGER,
    utilization_pct DECIMAL(5, 2),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_warehouses PRIMARY KEY (warehouse_id)
);

CREATE TABLE orders (
    order_id INTEGER NOT NULL,
    warehouse_id INTEGER,
    customer_name VARCHAR(255) NOT NULL,
    order_date DATE NOT NULL,
    status VARCHAR(32) NOT NULL,
    order_priority VARCHAR(32) NOT NULL,
    total_amount DECIMAL(15, 2) NOT NULL,
    currency_code CHAR(3) NOT NULL DEFAULT 'USD',
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_orders PRIMARY KEY (order_id)
);

CREATE TABLE shipments (
    shipment_id INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    carrier_id INTEGER NOT NULL,
    tracking_number VARCHAR(128) NOT NULL,
    ship_date DATE,
    delivery_date DATE,
    status VARCHAR(32) NOT NULL,
    shipping_cost DECIMAL(15, 2),
    currency_code CHAR(3) NOT NULL DEFAULT 'USD',
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_shipments PRIMARY KEY (shipment_id),
    CONSTRAINT fk_shipments_order
    FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT fk_shipments_carrier
    FOREIGN KEY (carrier_id) REFERENCES carriers (carrier_id)
);

CREATE TABLE inventory (
    inventory_id INTEGER NOT NULL,
    warehouse_id INTEGER NOT NULL,
    product_sku VARCHAR(64) NOT NULL,
    product_category VARCHAR(64),
    quantity_on_hand INTEGER NOT NULL,
    reorder_point INTEGER,
    last_updated_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_inventory PRIMARY KEY (inventory_id),
    CONSTRAINT fk_inventory_warehouse
    FOREIGN KEY (warehouse_id) REFERENCES warehouses (warehouse_id)
);
