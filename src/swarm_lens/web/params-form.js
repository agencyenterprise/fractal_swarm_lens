import { el } from "./ui.js";
import { field } from "./dialog.js";

const LONG_TEXT = 80;

// Reduces one JSON Schema property (pydantic `model_json_schema()`) to a form field description.
// Supported: string, number, integer, boolean, enums (inline or `$ref`), and Optional of these.
export function describeField(name, property, schema) {
  const resolve = (node) => (node?.$ref ? schema.$defs?.[node.$ref.split("/").at(-1)] : node);
  let nullable = false;
  let base = resolve(property);
  if (property.anyOf) {
    const options = property.anyOf.filter((option) => option.type !== "null");
    nullable = options.length < property.anyOf.length;
    if (options.length !== 1) throw new Error(`Parameter ${name} has an unsupported type.`);
    base = resolve(options[0]);
  }
  if (!base) throw new Error(`Parameter ${name} references an unknown definition.`);
  const kind = fieldKind(base, property.default);
  if (!kind) throw new Error(`Parameter ${name} has an unsupported type.`);
  return {
    name, kind, nullable,
    label: property.title || base.title || name,
    help: property.description || base.description || "",
    default: property.default,
    options: base.enum || (kind === "boolean" && nullable ? [true, false] : undefined),
    minimum: property.minimum ?? base.minimum,
    minLength: property.minLength ?? base.minLength,
    maxLength: property.maxLength ?? base.maxLength,
    maximum: property.maximum ?? base.maximum,
    required: (schema.required || []).includes(name),
  };
}

function fieldKind(base, fallback) {
  if (base.enum) return "enum";
  if (base.type === "string") {
    const long = typeof fallback === "string" && (fallback.length > LONG_TEXT || fallback.includes("\n"));
    return base.format === "textarea" || long ? "textarea" : "string";
  }
  return ["number", "integer", "boolean"].includes(base.type) ? base.type : null;
}

// The same id scheme as dialog.js `field`, prefixed so parameters never collide with a dialog's own fields.
const controlId = (spec) => `field-param-${spec.name}`;

// A nullable boolean has three states (unset, true, false), so it renders as a select like an enum.
const usesSelect = (spec) => spec.kind === "enum" || (spec.kind === "boolean" && spec.nullable);

function select(spec) {
  const input = el("select");
  input.id = controlId(spec);
  input.required = spec.required;
  if (!spec.required || spec.default === undefined) {
    const unset = el("option", "", "—");
    unset.value = "";
    input.append(unset);
  }
  spec.options.forEach((option, index) => {
    const node = el("option", "", spec.kind === "boolean" ? (option ? "Yes" : "No") : String(option));
    node.value = String(index);
    node.selected = option === spec.default;
    input.append(node);
  });
  const caption = el("label", "", spec.label);
  caption.htmlFor = input.id;
  const fragment = document.createDocumentFragment();
  fragment.append(caption, input);
  return { fragment, input };
}

function checkbox(spec) {
  const input = el("input");
  input.type = "checkbox";
  input.id = controlId(spec);
  input.checked = spec.default === true;
  const row = el("label", "pf-check");
  row.append(input, el("span", "", spec.label));
  return { fragment: row, input };
}

function textual(spec) {
  const control = field(spec.label, `param-${spec.name}`, spec.default ?? "", spec.kind === "textarea" ? "textarea" : "input");
  const { input } = control;
  if (spec.kind === "number" || spec.kind === "integer") {
    input.type = "number";
    input.step = spec.kind === "integer" ? "1" : "any";
    if (spec.minimum !== undefined) input.min = spec.minimum;
    if (spec.maximum !== undefined) input.max = spec.maximum;
  }
  if (spec.minLength !== undefined) input.minLength = spec.minLength;
  if (spec.maxLength !== undefined) input.maxLength = spec.maxLength;
  input.required = spec.required;
  return control;
}

function control(spec) {
  if (usesSelect(spec)) return select(spec);
  if (spec.kind === "boolean") return checkbox(spec);
  return textual(spec);
}

// Reads one control: `undefined` means "not set"; an Error explains a value that cannot be used.
function readValue(spec, input) {
  if (usesSelect(spec)) return input.value === "" ? undefined : spec.options[Number(input.value)];
  if (spec.kind === "boolean") return input.checked;
  const text = input.value;
  if (text.trim() === "") return undefined;
  if (spec.kind === "string" || spec.kind === "textarea") return text;
  const number = Number(text);
  if (!Number.isFinite(number)) return new Error(`${spec.label} must be a number.`);
  if (spec.kind === "integer" && !Number.isInteger(number)) return new Error(`${spec.label} must be a whole number.`);
  if (spec.minimum !== undefined && number < spec.minimum) return new Error(`${spec.label} must be at least ${spec.minimum}.`);
  if (spec.maximum !== undefined && number > spec.maximum) return new Error(`${spec.label} must be at most ${spec.maximum}.`);
  return number;
}

// `values()` returns typed values and omits fields left empty; `validate()` lists what blocks a submit.
export function renderParamsForm(schema = {}) {
  const fields = Object.entries(schema.properties || {}).map(([name, property]) => {
    const spec = describeField(name, property, schema);
    const { fragment, input } = control(spec);
    const node = el("div", "pf-field");
    node.append(fragment);
    if (spec.help) node.append(el("p", "pf-help muted", spec.help));
    return { spec, input, node };
  });
  const element = el("div", "params-form");
  element.append(...fields.map((item) => item.node));
  const read = () => fields.map(({ spec, input }) => [spec, readValue(spec, input)]);
  return {
    element,
    values: () => Object.fromEntries(read().filter(([, value]) => value !== undefined && !(value instanceof Error))
      .map(([spec, value]) => [spec.name, value])),
    validate: () => read().flatMap(([spec, value]) => {
      if (value instanceof Error) return [value.message];
      return value === undefined && spec.required ? [`${spec.label} is required.`] : [];
    }),
  };
}
