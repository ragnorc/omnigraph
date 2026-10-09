pub mod schema_ir;
pub mod schema_plan;
pub mod schema_shape;

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use arrow_schema::{DataType, Field, Schema, SchemaRef};

use crate::error::{CompilerError, Result};
use crate::schema::ast::{Cardinality, Constraint, ConstraintBound, SchemaDecl, SchemaFile};
use crate::types::{PropType, ScalarType};

#[derive(Debug, Clone)]
pub struct Catalog {
    pub node_types: HashMap<String, NodeType>,
    pub edge_types: HashMap<String, EdgeType>,
    /// This graph's system column spellings (RFC 0040); consumers must not
    /// hardcode any spelling.
    pub system_columns: schema_ir::SystemColumns,
    /// Maps normalized lowercase edge name -> EdgeType key (e.g. "knows" -> "Knows")
    pub edge_name_index: HashMap<String, String>,
    /// Interface declarations (for Phase 2 polymorphic queries)
    pub interfaces: HashMap<String, InterfaceType>,
    /// Source-only callers are intentionally unbound. Runtime catalogs are a
    /// direct projection of one validated accepted SchemaIR and retain that
    /// authority so no consumer can reconstruct IDs from mutable names.
    pub identity: CatalogIdentity,
}

#[derive(Debug, Clone)]
pub enum CatalogIdentity {
    SourceUnbound,
    Bound(Arc<schema_ir::SchemaIR>),
}

#[derive(Debug, Clone)]
pub struct InterfaceType {
    pub name: String,
    pub properties: HashMap<String, PropType>,
}

/// The `@embed` binding for a vector property: its source text property and,
/// optionally, the embedding model recorded by `@embed("source", model="…")`.
/// The model is what the query-time same-space check validates against.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EmbedSource {
    pub source: String,
    pub model: Option<String>,
}

#[derive(Debug, Clone)]
pub struct NodeType {
    pub name: String,
    /// Interface names this type implements
    pub implements: Vec<String>,
    pub properties: HashMap<String, PropType>,
    /// Key property names (from `@key` or `@key(name, ...)`). Runtime catalogs
    /// project composite members in stable property-ID order so renames cannot
    /// change physical tuple identity.
    pub key: Option<Vec<String>>,
    /// Uniqueness constraints (each entry is a list of property names)
    pub unique_constraints: Vec<Vec<String>>,
    /// Index declarations (each entry is a list of property names)
    pub indices: Vec<Vec<String>>,
    /// Value range constraints
    pub range_constraints: Vec<RangeConstraint>,
    /// Regex check constraints
    pub check_constraints: Vec<CheckConstraint>,
    /// Maps @embed target property -> its source text property + recorded model.
    pub embed_sources: HashMap<String, EmbedSource>,
    pub blob_properties: HashSet<String>,
    pub arrow_schema: SchemaRef,
}

impl NodeType {
    /// Backward-compatible accessor: returns the first (and typically only) key property name.
    pub fn key_property(&self) -> Option<&str> {
        self.key
            .as_ref()
            .and_then(|v| v.first())
            .map(|s| s.as_str())
    }

    /// The fields `return { $p }` projects: the identity column and the declared
    /// properties except `Blob` (T24) and `Vector`. Keyed on declared types: the
    /// engine rewrites Blob columns to their storage field before executing.
    pub fn node_object_fields(&self) -> impl Iterator<Item = &Arc<Field>> {
        self.arrow_schema
            .fields()
            .iter()
            .enumerate()
            .filter(|(index, field)| {
                let name = field.name().as_str();
                match self.properties.get(name) {
                    Some(prop) => {
                        !self.blob_properties.contains(name)
                            && !matches!(prop.scalar, ScalarType::Vector(_))
                    }
                    None => *index == 0,
                }
            })
            .map(|(_, field)| field)
    }

    /// [`Self::node_object_fields`] with the member name each field takes in
    /// the projected object: the identity is the meta-field `@id` on every
    /// vintage (RFC 0040), a declared property keeps its own name.
    pub fn node_object_members(&self) -> impl Iterator<Item = (&str, &Arc<Field>)> {
        self.node_object_fields().enumerate().map(|(index, field)| {
            let member = if index == 0 && !self.properties.contains_key(field.name().as_str()) {
                "@id"
            } else {
                field.name().as_str()
            };
            (member, field)
        })
    }
}

#[derive(Debug, Clone)]
pub struct RangeConstraint {
    pub property: String,
    pub min: Option<LiteralValue>,
    pub max: Option<LiteralValue>,
}

#[derive(Debug, Clone)]
pub enum LiteralValue {
    Integer(i64),
    Float(f64),
}

#[derive(Debug, Clone)]
pub struct CheckConstraint {
    pub property: String,
    pub pattern: String,
}

#[derive(Debug, Clone)]
pub struct EdgeType {
    pub name: String,
    /// The declared endpoint: a node type or, under `polymorphic-endpoints`,
    /// an interface. Use `from_members`/`to_members` for concrete tables.
    pub from_type: String,
    pub to_type: String,
    /// The concrete node types an endpoint admits, sorted by name: the
    /// declared node type alone, or every implementor of the declared
    /// interface.
    pub from_members: Vec<String>,
    pub to_members: Vec<String>,
    /// Whether a side stores the concrete endpoint type per row
    /// (`__src_type`/`__dst_type`), true exactly when it names an interface.
    pub src_tagged: bool,
    pub dst_tagged: bool,
    pub cardinality: Cardinality,
    pub properties: HashMap<String, PropType>,
    /// Key column names (from `@key(@src, @dst, ...)`), always including both
    /// endpoints. IR-bound catalogs order endpoints first (src, dst), then
    /// composite members in stable property-ID order so renames cannot
    /// change physical tuple identity. The parse-path catalog
    /// (`build_catalog`) keeps declared order and never feeds id
    /// derivation; only IR-bound catalogs do.
    pub key: Option<Vec<String>>,
    /// Uniqueness constraints on edge fields, including endpoint fields
    /// (e.g. `@unique(@src, @dst)`).
    pub unique_constraints: Vec<Vec<String>>,
    /// Index declarations on edge properties
    pub indices: Vec<Vec<String>>,
    pub blob_properties: HashSet<String>,
    pub arrow_schema: SchemaRef,
}

impl EdgeType {
    /// True when either side names an interface.
    pub fn is_polymorphic(&self) -> bool {
        self.src_tagged || self.dst_tagged
    }

    /// True when `node_type` may stand at the source end.
    pub fn admits_source(&self, node_type: &str) -> bool {
        self.from_members.iter().any(|member| member == node_type)
    }

    /// True when `node_type` may stand at the destination end.
    pub fn admits_destination(&self, node_type: &str) -> bool {
        self.to_members.iter().any(|member| member == node_type)
    }
}

/// The concrete members of a declared endpoint name: itself when it is a node
/// type, else every node type implementing the interface it names.
fn endpoint_members(
    declared: &str,
    node_implements: &[(String, Vec<String>)],
    is_node: bool,
) -> Vec<String> {
    if is_node {
        return vec![declared.to_string()];
    }
    let mut members = node_implements
        .iter()
        .filter(|(_, implements)| implements.iter().any(|name| name == declared))
        .map(|(name, _)| name.clone())
        .collect::<Vec<_>>();
    members.sort();
    members
}

impl Catalog {
    /// Concrete node types bound by a type name in a query: the node type
    /// itself, or every implementor of an interface. `None` when the name is
    /// neither. A name declared as both resolves to the node type.
    pub fn concrete_members(&self, type_name: &str) -> Option<Vec<String>> {
        if self.node_types.contains_key(type_name) {
            return Some(vec![type_name.to_string()]);
        }
        if !self.interfaces.contains_key(type_name) {
            return None;
        }
        let mut members = self
            .node_types
            .values()
            .filter(|node| node.implements.iter().any(|name| name == type_name))
            .map(|node| node.name.clone())
            .collect::<Vec<_>>();
        members.sort();
        Some(members)
    }

    /// The node type a query binding of `name` reads: the node type itself,
    /// or for an interface a virtual node type holding exactly the
    /// interface's declared properties, in name order after the id.
    pub fn binding_node_type(&self, name: &str) -> Option<std::borrow::Cow<'_, NodeType>> {
        if let Some(node) = self.node_types.get(name) {
            return Some(std::borrow::Cow::Borrowed(node));
        }
        let interface = self.interfaces.get(name)?;
        let mut names = interface.properties.keys().cloned().collect::<Vec<_>>();
        names.sort();
        let mut fields = vec![Field::new(self.system_columns.id, DataType::Utf8, false)];
        fields.extend(names.iter().map(|property| {
            let prop_type = &interface.properties[property];
            Field::new(property, prop_type.to_arrow(), prop_type.nullable)
        }));
        let blob_properties = interface
            .properties
            .iter()
            .filter(|(_, prop)| matches!(prop.scalar, ScalarType::Blob))
            .map(|(name, _)| name.clone())
            .collect();
        Some(std::borrow::Cow::Owned(NodeType {
            name: name.to_string(),
            implements: Vec::new(),
            properties: interface.properties.clone(),
            key: None,
            unique_constraints: Vec::new(),
            indices: Vec::new(),
            range_constraints: Vec::new(),
            check_constraints: Vec::new(),
            embed_sources: HashMap::new(),
            blob_properties,
            arrow_schema: Arc::new(Schema::new(fields)),
        }))
    }

    /// True when `name` binds more than its own table: an interface.
    pub fn is_abstract_type(&self, name: &str) -> bool {
        !self.node_types.contains_key(name) && self.interfaces.contains_key(name)
    }

    /// `narrow` admits no node type `wide` does not: equal names, or a
    /// nonempty member set contained in `wide`'s.
    pub fn type_fits(&self, narrow: &str, wide: &str) -> bool {
        if narrow == wide {
            return true;
        }
        match (self.concrete_members(narrow), self.concrete_members(wide)) {
            (Some(narrow), Some(wide)) => {
                !narrow.is_empty() && narrow.iter().all(|member| wide.contains(member))
            }
            _ => false,
        }
    }

    pub fn lookup_edge_by_name(&self, name: &str) -> Option<&EdgeType> {
        if let Some(et) = self.edge_types.get(name) {
            return Some(et);
        }
        if let Some(key) = self.edge_name_index.get(&normalize_edge_name(name)) {
            return self.edge_types.get(key);
        }
        None
    }

    pub fn is_identity_bound(&self) -> bool {
        matches!(self.identity, CatalogIdentity::Bound(_))
    }

    pub fn bound_schema_ir(&self) -> Option<&schema_ir::SchemaIR> {
        match &self.identity {
            CatalogIdentity::SourceUnbound => None,
            CatalogIdentity::Bound(ir) => Some(ir),
        }
    }

    pub fn type_id(&self, name: &str) -> Option<schema_ir::StableTypeId> {
        let mut matches = [
            self.interface_type_id(name),
            self.node_type_id(name),
            self.edge_type_id(name),
        ]
        .into_iter()
        .flatten();
        let identity = matches.next()?;
        matches.next().is_none().then_some(identity)
    }

    pub fn interface_type_id(&self, name: &str) -> Option<schema_ir::StableTypeId> {
        self.bound_schema_ir()?
            .interfaces
            .iter()
            .find(|entry| entry.name == name)
            .map(|entry| entry.type_id)
    }

    pub fn node_type_id(&self, name: &str) -> Option<schema_ir::StableTypeId> {
        self.bound_schema_ir()?
            .nodes
            .iter()
            .find(|entry| entry.name == name)
            .map(|entry| entry.type_id)
    }

    pub fn edge_type_id(&self, name: &str) -> Option<schema_ir::StableTypeId> {
        self.bound_schema_ir()?
            .edges
            .iter()
            .find(|entry| entry.name == name)
            .map(|entry| entry.type_id)
    }

    pub fn table_incarnation_id(&self, name: &str) -> Option<schema_ir::TableIncarnationId> {
        let mut matches = [
            self.node_table_incarnation_id(name),
            self.edge_table_incarnation_id(name),
        ]
        .into_iter()
        .flatten();
        let identity = matches.next()?;
        matches.next().is_none().then_some(identity)
    }

    pub fn node_table_incarnation_id(&self, name: &str) -> Option<schema_ir::TableIncarnationId> {
        self.bound_schema_ir()?
            .nodes
            .iter()
            .find(|entry| entry.name == name)
            .map(|entry| entry.table_incarnation_id)
    }

    pub fn edge_table_incarnation_id(&self, name: &str) -> Option<schema_ir::TableIncarnationId> {
        self.bound_schema_ir()?
            .edges
            .iter()
            .find(|entry| entry.name == name)
            .map(|entry| entry.table_incarnation_id)
    }

    pub fn property_id(
        &self,
        owner_name: &str,
        property_name: &str,
    ) -> Option<schema_ir::StablePropertyId> {
        let mut owners = [
            (
                self.interface_type_id(owner_name),
                self.interface_property_id(owner_name, property_name),
            ),
            (
                self.node_type_id(owner_name),
                self.node_property_id(owner_name, property_name),
            ),
            (
                self.edge_type_id(owner_name),
                self.edge_property_id(owner_name, property_name),
            ),
        ]
        .into_iter()
        .filter(|(owner, _)| owner.is_some());
        let (_, property) = owners.next()?;
        if owners.next().is_some() {
            None
        } else {
            property
        }
    }

    pub fn interface_property_id(
        &self,
        owner_name: &str,
        property_name: &str,
    ) -> Option<schema_ir::StablePropertyId> {
        self.bound_schema_ir()?
            .interfaces
            .iter()
            .find(|entry| entry.name == owner_name)?
            .properties
            .iter()
            .find(|property| property.name == property_name)
            .map(|property| property.property_id)
    }

    pub fn node_property_id(
        &self,
        owner_name: &str,
        property_name: &str,
    ) -> Option<schema_ir::StablePropertyId> {
        self.bound_schema_ir()?
            .nodes
            .iter()
            .find(|entry| entry.name == owner_name)?
            .properties
            .iter()
            .find(|property| property.name == property_name)
            .map(|property| property.property_id)
    }

    pub fn edge_property_id(
        &self,
        owner_name: &str,
        property_name: &str,
    ) -> Option<schema_ir::StablePropertyId> {
        self.bound_schema_ir()?
            .edges
            .iter()
            .find(|entry| entry.name == owner_name)?
            .properties
            .iter()
            .find(|property| property.name == property_name)
            .map(|property| property.property_id)
    }
}

fn normalize_edge_name(name: &str) -> String {
    name.to_lowercase()
}

fn bound_to_literal(b: &ConstraintBound) -> LiteralValue {
    match b {
        ConstraintBound::Integer(n) => LiteralValue::Integer(*n),
        ConstraintBound::Float(f) => LiteralValue::Float(*f),
    }
}

/// Builds a catalog from `.pg` source. Source-only catalogs bind to no stored
/// graph, so they carry the current system column spellings; a stored graph's
/// own spellings come from `build_catalog_from_ir`.
pub fn build_catalog(schema: &SchemaFile) -> Result<Catalog> {
    let system_columns = schema_ir::SYSTEM_COLUMNS_V3;
    let mut node_types = HashMap::new();
    let mut edge_types = HashMap::new();
    let mut edge_name_index = HashMap::new();
    let mut interfaces = HashMap::new();

    // Pass 0: collect interfaces
    for decl in &schema.declarations {
        if let SchemaDecl::Interface(iface) = decl {
            let mut properties = HashMap::new();
            for prop in &iface.properties {
                properties.insert(prop.name.clone(), prop.prop_type.clone());
            }
            interfaces.insert(
                iface.name.clone(),
                InterfaceType {
                    name: iface.name.clone(),
                    properties,
                },
            );
        }
    }

    // Pass 1: collect node types
    for decl in &schema.declarations {
        if let SchemaDecl::Node(node) = decl {
            if node_types.contains_key(&node.name) {
                return Err(CompilerError::Catalog(format!(
                    "duplicate node type: {}",
                    node.name
                )));
            }

            let mut properties = HashMap::new();
            let mut embed_sources = HashMap::new();
            let mut blob_properties = HashSet::new();
            for prop in &node.properties {
                properties.insert(prop.name.clone(), prop.prop_type.clone());
                if matches!(prop.prop_type.scalar, ScalarType::Blob) {
                    blob_properties.insert(prop.name.clone());
                }
                // Extract @embed: the source text property (positional) and the
                // optional recorded model (the `model` kwarg).
                if let Some(ann) = prop.annotations.iter().find(|ann| ann.name == "embed") {
                    if let Some(source) = ann.value.clone() {
                        embed_sources.insert(
                            prop.name.clone(),
                            EmbedSource {
                                source,
                                model: ann.kwargs.get("model").cloned(),
                            },
                        );
                    }
                }
            }

            // Extract constraints from the typed Constraint enum
            let mut key: Option<Vec<String>> = None;
            let mut unique_constraints = Vec::new();
            let mut indices = Vec::new();
            let mut range_constraints = Vec::new();
            let mut check_constraints = Vec::new();

            for constraint in &node.constraints {
                match constraint {
                    Constraint::Key(cols) => {
                        key = Some(cols.clone());
                        // @key implies index on key columns
                        indices.push(cols.clone());
                    }
                    Constraint::Unique(cols) => {
                        unique_constraints.push(cols.clone());
                    }
                    Constraint::Index(cols) => {
                        indices.push(cols.clone());
                    }
                    Constraint::Range { property, min, max } => {
                        range_constraints.push(RangeConstraint {
                            property: property.clone(),
                            min: min.as_ref().map(bound_to_literal),
                            max: max.as_ref().map(bound_to_literal),
                        });
                    }
                    Constraint::Check { property, pattern } => {
                        check_constraints.push(CheckConstraint {
                            property: property.clone(),
                            pattern: pattern.clone(),
                        });
                    }
                }
            }

            let mut fields = vec![Field::new(system_columns.id, DataType::Utf8, false)];
            for prop in &node.properties {
                fields.push(Field::new(
                    &prop.name,
                    prop.prop_type.to_arrow(),
                    prop.prop_type.nullable,
                ));
            }
            let arrow_schema = Arc::new(Schema::new(fields));

            node_types.insert(
                node.name.clone(),
                NodeType {
                    name: node.name.clone(),
                    implements: node.implements.clone(),
                    properties,
                    key,
                    unique_constraints,
                    indices,
                    range_constraints,
                    check_constraints,
                    embed_sources,
                    blob_properties,
                    arrow_schema,
                },
            );
        }
    }

    // Pass 2: collect edge types, validate endpoints
    for decl in &schema.declarations {
        if let SchemaDecl::Edge(edge) = decl {
            if edge_types.contains_key(&edge.name) {
                return Err(CompilerError::Catalog(format!(
                    "duplicate edge type: {}",
                    edge.name
                )));
            }
            if !node_types.contains_key(&edge.from_type) && !interfaces.contains_key(&edge.from_type) {
                return Err(CompilerError::Catalog(format!(
                    "edge {} references unknown source type: {}",
                    edge.name, edge.from_type
                )));
            }
            if !node_types.contains_key(&edge.to_type) && !interfaces.contains_key(&edge.to_type) {
                return Err(CompilerError::Catalog(format!(
                    "edge {} references unknown target type: {}",
                    edge.name, edge.to_type
                )));
            }

            let mut properties = HashMap::new();
            let mut blob_properties = HashSet::new();
            let node_implements = node_types
                .values()
                .map(|node: &NodeType| (node.name.clone(), node.implements.clone()))
                .collect::<Vec<_>>();
            let src_tagged = !node_types.contains_key(&edge.from_type);
            refuse_polymorphic_source_card(&edge.name, src_tagged, &edge.cardinality)?;
            let dst_tagged = !node_types.contains_key(&edge.to_type);
            let from_members = endpoint_members(&edge.from_type, &node_implements, !src_tagged);
            let to_members = endpoint_members(&edge.to_type, &node_implements, !dst_tagged);
            let mut fields = vec![
                Field::new(system_columns.id, DataType::Utf8, false),
                Field::new(system_columns.src, DataType::Utf8, false),
                Field::new(system_columns.dst, DataType::Utf8, false),
            ];
            for prop in &edge.properties {
                properties.insert(prop.name.clone(), prop.prop_type.clone());
                if matches!(prop.prop_type.scalar, ScalarType::Blob) {
                    blob_properties.insert(prop.name.clone());
                }
                fields.push(Field::new(
                    &prop.name,
                    prop.prop_type.to_arrow(),
                    prop.prop_type.nullable,
                ));
            }

            // Tag columns follow every user property, so positional readers
            // that treat columns 3.. as properties must skip them explicitly.
            if src_tagged {
                fields.push(Field::new(schema_ir::EDGE_SRC_TYPE_COLUMN, DataType::UInt64, true));
            }
            if dst_tagged {
                fields.push(Field::new(schema_ir::EDGE_DST_TYPE_COLUMN, DataType::UInt64, true));
            }

            // Extract edge constraints
            let mut key: Option<Vec<String>> = None;
            let mut unique_constraints = Vec::new();
            let mut edge_indices = Vec::new();
            for constraint in &edge.constraints {
                match constraint {
                    Constraint::Key(cols) => {
                        // No implied index: edge keys are always composite and
                        // index maintenance builds single-column indices only;
                        // the fixed id/src/dst BTREEs already cover endpoints.
                        key = Some(cols.clone());
                    }
                    Constraint::Unique(cols) => unique_constraints.push(cols.clone()),
                    Constraint::Index(cols) => edge_indices.push(cols.clone()),
                    _ => {} // Range/Check validated at parse time to not appear on edges
                }
            }

            let normalized_name = normalize_edge_name(&edge.name);
            if let Some(existing) = edge_name_index.get(&normalized_name)
                && existing != &edge.name
            {
                return Err(CompilerError::Catalog(format!(
                    "edge name collision after case folding: '{}' conflicts with '{}'",
                    edge.name, existing
                )));
            }
            edge_name_index.insert(normalized_name, edge.name.clone());

            edge_types.insert(
                edge.name.clone(),
                EdgeType {
                    name: edge.name.clone(),
                    from_type: edge.from_type.clone(),
                    to_type: edge.to_type.clone(),
                    from_members,
                    to_members,
                    src_tagged,
                    dst_tagged,
                    cardinality: edge.cardinality.clone(),
                    properties,
                    key: key.map(|columns| {
                        keyed_with_endpoint_types(columns, system_columns, src_tagged, dst_tagged)
                    }),
                    unique_constraints,
                    indices: edge_indices,
                    blob_properties,
                    arrow_schema: Arc::new(Schema::new(fields)),
                },
            );
        }
    }

    Ok(Catalog {
        node_types,
        edge_types,
        edge_name_index,
        interfaces,
        system_columns,
        identity: CatalogIdentity::SourceUnbound,
    })
}

/// `@card` counts edges per source id, and the validator reads deletions from
/// the source's one node table: neither holds when the source is an
/// interface, so the prototype refuses the combination.
fn refuse_polymorphic_source_card(
    edge_name: &str,
    src_tagged: bool,
    cardinality: &crate::schema::ast::Cardinality,
) -> Result<()> {
    if src_tagged && !cardinality.is_default() {
        return Err(CompilerError::Catalog(format!(
            "edge '{edge_name}': @card on an edge whose source is an interface is not supported"
        )));
    }
    Ok(())
}

/// An edge key over a polymorphic endpoint: the endpoint id names a node only
/// together with its concrete type, so the side's tag column follows the
/// endpoint in the key (polymorphic types prototype).
fn keyed_with_endpoint_types(
    columns: Vec<String>,
    system_columns: schema_ir::SystemColumns,
    src_tagged: bool,
    dst_tagged: bool,
) -> Vec<String> {
    let mut keyed = Vec::with_capacity(columns.len() + 2);
    for column in columns {
        let tag = if src_tagged && column == system_columns.src {
            Some(schema_ir::EDGE_SRC_TYPE_COLUMN)
        } else if dst_tagged && column == system_columns.dst {
            Some(schema_ir::EDGE_DST_TYPE_COLUMN)
        } else {
            None
        };
        keyed.push(column);
        keyed.extend(tag.map(str::to_string));
    }
    keyed
}

/// Build the runtime catalog directly from validated accepted identity
/// authority. This path never round-trips through the source AST and never
/// mints or derives an identity from a name.
pub fn build_catalog_from_ir(ir: &schema_ir::SchemaIR) -> Result<Catalog> {
    schema_ir::validate_schema_ir(ir)?;
    let system_columns = ir.system_columns();

    let interfaces = ir
        .interfaces
        .iter()
        .map(|interface| {
            (
                interface.name.clone(),
                InterfaceType {
                    name: interface.name.clone(),
                    properties: interface
                        .properties
                        .iter()
                        .map(|property| (property.name.clone(), property.prop_type.clone()))
                        .collect(),
                },
            )
        })
        .collect::<HashMap<_, _>>();

    let mut node_types = HashMap::new();
    for node in &ir.nodes {
        let properties = node
            .properties
            .iter()
            .map(|property| (property.name.clone(), property.prop_type.clone()))
            .collect::<HashMap<_, _>>();
        let blob_properties = node
            .properties
            .iter()
            .filter(|property| matches!(property.prop_type.scalar, ScalarType::Blob))
            .map(|property| property.name.clone())
            .collect();
        let embed_sources = node
            .properties
            .iter()
            .filter_map(|property| {
                property.embed_source.as_ref().map(|embed| {
                    (
                        property.name.clone(),
                        EmbedSource {
                            source: embed.source.property_name.clone(),
                            model: embed.model.clone(),
                        },
                    )
                })
            })
            .collect();
        let mut key = None;
        let mut unique_constraints = Vec::new();
        let mut indices = Vec::new();
        let mut range_constraints = Vec::new();
        let mut check_constraints = Vec::new();
        for constraint in &node.constraints {
            if let schema_ir::ConstraintIR::Key { fields } = constraint {
                let mut stable_fields = fields
                    .iter()
                    .map(|field| match field {
                        schema_ir::FieldRefIR::Property(reference) => {
                            Ok((reference.property_id, reference.property_name.clone()))
                        }
                        schema_ir::FieldRefIR::System(_) => Err(CompilerError::Catalog(format!(
                            "node '{}' @key must reference declared properties",
                            node.name
                        ))),
                    })
                    .collect::<Result<Vec<_>>>()?;
                stable_fields.sort_by_key(|(property_id, _)| *property_id);
                if stable_fields.is_empty() {
                    return Err(CompilerError::Catalog(format!(
                        "node '{}' @key cannot be empty",
                        node.name
                    )));
                }
                if stable_fields.windows(2).any(|pair| pair[0].0 == pair[1].0) {
                    return Err(CompilerError::Catalog(format!(
                        "node '{}' @key repeats a property identity",
                        node.name
                    )));
                }
                let columns = stable_fields
                    .into_iter()
                    .map(|(_, property_name)| property_name)
                    .collect::<Vec<_>>();
                key = Some(columns.clone());
                indices.push(columns);
                continue;
            }
            match schema_ir::physical_constraint_from_ir(constraint, system_columns) {
                Constraint::Key(_) => unreachable!("@key handled in stable property-id order"),
                Constraint::Unique(columns) => unique_constraints.push(columns),
                Constraint::Index(columns) => indices.push(columns),
                Constraint::Range { property, min, max } => {
                    range_constraints.push(RangeConstraint {
                        property,
                        min: min.as_ref().map(bound_to_literal),
                        max: max.as_ref().map(bound_to_literal),
                    });
                }
                Constraint::Check { property, pattern } => {
                    check_constraints.push(CheckConstraint { property, pattern });
                }
            }
        }
        let mut fields = vec![Field::new(system_columns.id, DataType::Utf8, false)];
        fields.extend(node.properties.iter().map(|property| {
            Field::new(
                &property.name,
                property.prop_type.to_arrow(),
                property.prop_type.nullable,
            )
        }));
        node_types.insert(
            node.name.clone(),
            NodeType {
                name: node.name.clone(),
                implements: node
                    .implements
                    .iter()
                    .map(|reference| reference.type_name.clone())
                    .collect(),
                properties,
                key,
                unique_constraints,
                indices,
                range_constraints,
                check_constraints,
                embed_sources,
                blob_properties,
                arrow_schema: Arc::new(Schema::new(fields)),
            },
        );
    }

    let mut edge_types = HashMap::new();
    let mut edge_name_index = HashMap::new();
    for edge in &ir.edges {
        let properties = edge
            .properties
            .iter()
            .map(|property| (property.name.clone(), property.prop_type.clone()))
            .collect::<HashMap<_, _>>();
        let blob_properties = edge
            .properties
            .iter()
            .filter(|property| matches!(property.prop_type.scalar, ScalarType::Blob))
            .map(|property| property.name.clone())
            .collect();
        let mut key = None;
        let mut unique_constraints = Vec::new();
        let mut indices = Vec::new();
        for constraint in &edge.constraints {
            if let schema_ir::ConstraintIR::Key { fields } = constraint {
                let mut has_src = false;
                let mut has_dst = false;
                let mut stable_fields = Vec::new();
                for field in fields {
                    match field {
                        schema_ir::FieldRefIR::System(reference) => match reference.role {
                            schema_ir::SystemFieldRole::Src => {
                                if has_src {
                                    return Err(CompilerError::Catalog(format!(
                                        "edge '{}' @key repeats an endpoint",
                                        edge.name
                                    )));
                                }
                                has_src = true;
                            }
                            schema_ir::SystemFieldRole::Dst => {
                                if has_dst {
                                    return Err(CompilerError::Catalog(format!(
                                        "edge '{}' @key repeats an endpoint",
                                        edge.name
                                    )));
                                }
                                has_dst = true;
                            }
                            schema_ir::SystemFieldRole::Id => {
                                return Err(CompilerError::Catalog(format!(
                                    "edge '{}' @key cannot reference the id",
                                    edge.name
                                )));
                            }
                        },
                        schema_ir::FieldRefIR::Property(reference) => {
                            stable_fields
                                .push((reference.property_id, reference.property_name.clone()));
                        }
                    }
                }
                if !has_src || !has_dst {
                    return Err(CompilerError::Catalog(format!(
                        "edge '{}' @key must include both endpoints",
                        edge.name
                    )));
                }
                stable_fields.sort_by_key(|(property_id, _)| *property_id);
                if stable_fields.windows(2).any(|pair| pair[0].0 == pair[1].0) {
                    return Err(CompilerError::Catalog(format!(
                        "edge '{}' @key repeats a property identity",
                        edge.name
                    )));
                }
                let mut columns = vec![
                    system_columns.src.to_string(),
                    system_columns.dst.to_string(),
                ];
                columns.extend(
                    stable_fields
                        .into_iter()
                        .map(|(_, property_name)| property_name),
                );
                // No implied index: edge keys are always composite and index
                // maintenance builds single-column indices only; the fixed
                // id/src/dst BTREEs already cover endpoints.
                key = Some(columns);
                continue;
            }
            match schema_ir::physical_constraint_from_ir(constraint, system_columns) {
                Constraint::Key(_) => unreachable!("@key handled in stable property-id order"),
                Constraint::Unique(columns) => unique_constraints.push(columns),
                Constraint::Index(columns) => indices.push(columns),
                _ => {}
            }
        }
        let node_implements = node_types
            .values()
            .map(|node: &NodeType| (node.name.clone(), node.implements.clone()))
            .collect::<Vec<_>>();
        let src_tagged = !node_types.contains_key(&edge.from_type.type_name);
        refuse_polymorphic_source_card(&edge.name, src_tagged, &edge.cardinality)?;
        let dst_tagged = !node_types.contains_key(&edge.to_type.type_name);
        let from_members =
            endpoint_members(&edge.from_type.type_name, &node_implements, !src_tagged);
        let to_members = endpoint_members(&edge.to_type.type_name, &node_implements, !dst_tagged);
        let mut fields = vec![
            Field::new(system_columns.id, DataType::Utf8, false),
            Field::new(system_columns.src, DataType::Utf8, false),
            Field::new(system_columns.dst, DataType::Utf8, false),
        ];
        fields.extend(edge.properties.iter().map(|property| {
            Field::new(
                &property.name,
                property.prop_type.to_arrow(),
                property.prop_type.nullable,
            )
        }));
        // Tag columns follow every user property (see build_catalog).
        if src_tagged {
            fields.push(Field::new(schema_ir::EDGE_SRC_TYPE_COLUMN, DataType::UInt64, true));
        }
        if dst_tagged {
            fields.push(Field::new(schema_ir::EDGE_DST_TYPE_COLUMN, DataType::UInt64, true));
        }
        let normalized_name = normalize_edge_name(&edge.name);
        if let Some(existing) = edge_name_index.get(&normalized_name)
            && existing != &edge.name
        {
            return Err(CompilerError::Catalog(format!(
                "edge name collision after case folding: '{}' conflicts with '{}'",
                edge.name, existing
            )));
        }
        edge_name_index.insert(normalized_name, edge.name.clone());
        edge_types.insert(
            edge.name.clone(),
            EdgeType {
                name: edge.name.clone(),
                from_type: edge.from_type.type_name.clone(),
                to_type: edge.to_type.type_name.clone(),
                from_members,
                to_members,
                src_tagged,
                dst_tagged,
                cardinality: edge.cardinality.clone(),
                properties,
                key: key.map(|columns| {
                    keyed_with_endpoint_types(columns, system_columns, src_tagged, dst_tagged)
                }),
                unique_constraints,
                indices,
                blob_properties,
                arrow_schema: Arc::new(Schema::new(fields)),
            },
        );
    }

    Ok(Catalog {
        node_types,
        edge_types,
        edge_name_index,
        interfaces,
        system_columns,
        identity: CatalogIdentity::Bound(Arc::new(ir.clone())),
    })
}

#[cfg(test)]
#[path = "tests.rs"]
mod tests;
