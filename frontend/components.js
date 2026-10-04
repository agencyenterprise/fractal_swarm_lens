import * as select from '@zag-js/select';
import * as combobox from '@zag-js/combobox';
import { VanillaMachine, normalizeProps, spreadProps } from '@zag-js/vanilla';

const pickers = new WeakMap();
let sequence = 0;
function node(tag, className = '', text = '') {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}
const ICONS = {
  check: 'm5 12 4 4L19 6',
  search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  chevron: 'm8 9 4-4 4 4m-8 6 4 4 4-4',
  star: 'M12 3.6l2.55 5.17 5.7.83-4.13 4.02.98 5.68L12 16.6l-5.1 2.7.98-5.68L3.75 9.6l5.7-.83z',
};
function icon(kind) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7', 'aria-hidden': 'true' })) element.setAttribute(key, value);
  element.classList.add('size-4', 'shrink-0');
  const path = document.createElementNS(element.namespaceURI, 'path');
  path.setAttribute('d', ICONS[kind]);
  path.setAttribute('stroke-linecap', 'round');
  path.setAttribute('stroke-linejoin', 'round');
  element.append(path);
  return element;
}

/**
 * Zag handles focus, dismissal, typeahead, ARIA, and keyboard selection.
 * `favorites` ({ has(value), toggle(value) }) adds a star to each option and a "Favorites only" filter.
 */
export class Picker {
  constructor({ id, name = '', label, heading = label, searchable = false, dark = false, compact = false, hideLabel = false,
    placeholder = 'Choose an option', value = '', options = [], favorites = null }) {
    this.id = id || `picker-${++sequence}`;
    this.searchable = searchable;
    this.library = searchable ? combobox : select;
    this.name = name;
    this._value = String(value);
    this._disabled = false;
    this._required = false;
    this.options = [];
    this.rows = [];
    this.labelText = label;
    this.headingText = heading;
    this.placeholder = placeholder;
    this.favorites = favorites;
    this.favoritesOnly = false;
    this.query = '';
    this.root = node('div', `ui-picker min-w-0 ${dark ? 'picker-dark' : ''} ${compact ? 'picker-compact' : ''}`);
    this.root.dataset.pickerRoot = '';
    this.label = node('label', hideLabel ? 'sr-only' : 'ui-label', label);
    this.control = node('div', 'picker-control');
    this.trigger = node('button', searchable ? 'picker-toggle' : 'picker-trigger');
    this.trigger.type = 'button';
    this.text = node('span', 'min-w-0 flex-1 truncate text-left');
    this.meta = node('span', 'picker-selected-meta');
    this.meta.hidden = true;
    this.hiddenInput = node(searchable ? 'input' : 'select');
    if (searchable) this.hiddenInput.type = 'hidden';
    this.hiddenInput.name = name;
    this.hiddenInput.hidden = true;
    if (searchable) {
      this.input = node('input', 'picker-search-input');
      this.input.placeholder = placeholder;
      this.control.append(icon('search'), this.input, this.trigger);
      this.input.addEventListener('focus', () => this.input.select());
    } else {
      this.trigger.append(this.text);
      this.control.append(this.trigger);
    }
    this.trigger.append(icon('chevron'));
    this.root.append(this.label, this.control, this.meta, this.hiddenInput);
    this.positioner = node('div', 'picker-positioner z-[100]!');
    this.positioner.dataset.searchable = String(searchable);
    this.panel = node('div', 'picker-panel');
    this.heading = node('div', 'picker-panel-heading');
    this.headingLabel = node('span');
    this.heading.append(this.headingLabel);
    if (favorites) this.heading.append(this.favoritesFilter());
    this.list = node('div', 'picker-list');
    this.empty = node('div', 'picker-empty');
    this.empty.setAttribute('role', 'status');
    this.footer = node('div', 'picker-footer', searchable ? '↑ ↓ Navigate    ↵ Open    esc Close' : '↑ ↓ Navigate    ↵ Select');
    this.panel.append(this.heading, this.list, this.empty, this.footer);
    this.positioner.append(this.panel);
    this.positioner.hidden = true;
    pickers.set(this.root, this);
    this.setOptions(options);
  }

  get value() { return this._value; }
  set value(value) {
    this._value = String(value ?? '');
    this.silent = true;
    try {
      this.api?.setValue(this._value ? [this._value] : []);
      if (this.searchable) this.api?.setInputValue(this.options.find(item => item.value === this._value)?.label || '');
    } finally { this.silent = false; }
    this.renderValue();
  }
  get disabled() { return this._disabled; }
  set disabled(value) { this._disabled = Boolean(value); this.machine?.updateProps({ disabled: this._disabled }); }
  get required() { return this._required; }
  set required(value) { this._required = Boolean(value); this.machine?.updateProps({ required: this._required }); }
  focus() { (this.input || this.trigger).focus(); }

  setOptions(options) {
    this.options = options.map(option => ({ ...option, value: String(option.value) }));
    if (!this.options.some(item => item.value === this.value)) this._value = this.options.find(item => !item.disabled)?.value || '';
    if (!this.searchable) this.hiddenInput.replaceChildren(...this.options.map(item => {
      const option = node('option', '', item.label);
      option.value = item.value;
      option.disabled = Boolean(item.disabled);
      return option;
    }));
    this.setCollection(this.options);
    this.value = this._value;
  }

  favoritesFilter() {
    const toggle = node('button', 'picker-favorites-filter');
    toggle.type = 'button';
    toggle.append(icon('star'), node('span', '', 'Favorites only'));
    toggle.setAttribute('aria-pressed', 'false');
    keepOpen(toggle, () => {
      this.favoritesOnly = !this.favoritesOnly;
      toggle.setAttribute('aria-pressed', String(this.favoritesOnly));
      this.setCollection(this.visibleOptions());
    });
    return toggle;
  }

  favoriteStar(item) {
    const star = node('button', 'picker-star');
    star.type = 'button';
    star.tabIndex = -1;
    star.append(icon('star'));
    const sync = () => {
      const on = this.favorites.has(item.value);
      star.setAttribute('aria-pressed', String(on));
      star.setAttribute('aria-label', on ? `Remove ${item.label} from favorites` : `Add ${item.label} to favorites`);
    };
    sync();
    keepOpen(star, () => {
      this.favorites.toggle(item.value);
      if (this.favoritesOnly) this.setCollection(this.visibleOptions());
      else sync();
    });
    return star;
  }

  visibleOptions() {
    const query = this.query.toLocaleLowerCase().trim();
    return this.options.filter(item => (!this.favoritesOnly || this.favorites.has(item.value))
      && (!query || [item.label, item.description, item.detail, item.badge].join(' ').toLocaleLowerCase().includes(query)));
  }

  setCollection(items) {
    this.collection = this.library.collection({ items, itemToValue: item => item.value,
      itemToString: item => item.label, isItemDisabled: item => Boolean(item.disabled) });
    this.rows = items.map(item => {
      const row = node('div', 'picker-option');
      const copy = node('div', 'min-w-0 flex-1');
      const line = node('div', 'flex items-start justify-between gap-3');
      const title = node('span', 'picker-option-title', item.label);
      line.append(title);
      if (item.badge) line.append(node('span', 'picker-badge', item.badge));
      copy.append(line);
      if (item.description) copy.append(node('span', 'picker-option-description', item.description));
      if (item.detail) copy.append(node('span', 'picker-option-detail', item.detail));
      const check = node('span', 'picker-check');
      check.append(icon('check'));
      row.append(copy, check);
      if (this.favorites) row.prepend(this.favoriteStar(item));
      return { item, row, title, check };
    });
    this.list.replaceChildren(...this.rows.map(row => row.row));
    this.empty.hidden = items.length !== 0;
    this.empty.textContent = this.favoritesOnly && !this.query ? 'No favorites yet. Star an item to add it.' : 'No matches';
    this.headingLabel.textContent = items.length === this.options.length ? `${this.headingText} · ${items.length}`
      : `${this.headingText} · ${items.length} of ${this.options.length}`;
    this.machine?.updateProps({ collection: this.collection });
    if (this.machine) this.render();
  }

  mount() {
    if (this.machine || !this.root.isConnected) return this;
    (this.root.closest('dialog') || document.body).append(this.positioner);
    const props = {
      id: this.id, collection: this.collection, name: this.searchable ? undefined : this.name,
      defaultValue: this.value ? [this.value] : [], disabled: this.disabled, required: this.required,
      positioning: { placement: 'bottom-start', strategy: 'fixed', gutter: 7, sameWidth: !this.searchable, overflowPadding: 12 },
      // The panel header holds controls (Favorites only); using them must not dismiss the panel.
      onInteractOutside: (event) => {
        if (this.panel.contains(event.detail?.target)) event.preventDefault();
      },
      onValueChange: ({ value }) => {
        const previous = this._value;
        this._value = value[0] || '';
        this.renderValue();
        if (!this.silent && previous !== this.value) this.onchange?.({ target: this });
      },
      onOpenChange: ({ open, reason }) => {
        if (this.searchable && open && reason !== 'input-change') {
          this.query = '';
          this.setCollection(this.visibleOptions());
          queueMicrotask(() => this.input.select());
        }
        if (this.searchable && !open) queueMicrotask(() => {
          if (!this.machine) return;
          this.query = '';
          this.setCollection(this.visibleOptions());
          this.api.setInputValue(this.options.find(item => item.value === this.value)?.label || '');
          this.input.scrollLeft = 0;
        });
      },
    };
    if (this.searchable) Object.assign(props, {
      placeholder: this.placeholder, openOnClick: true, inputBehavior: 'autohighlight',
      defaultInputValue: this.options.find(item => item.value === this.value)?.label || '',
      onInputValueChange: ({ inputValue, reason }) => {
        if (reason !== 'input-change') return;
        this.query = inputValue;
        this.setCollection(this.visibleOptions());
      },
    });
    this.machine = new VanillaMachine(this.library.machine, props);
    this.unsubscribe = this.machine.subscribe(() => this.render());
    this.render();
    this.machine.start();
    return this;
  }

  renderValue() {
    const item = this.options.find(item => item.value === this.value);
    this.hiddenInput.value = this.value;
    this.text.textContent = item?.label || this.placeholder;
    (this.input || this.trigger).title = [item?.label, item?.description].filter(Boolean).join(' · ');
    this.meta.textContent = this.searchable ? item?.description || '' : '';
    this.meta.hidden = !this.meta.textContent;
    if (this.searchable && !this.machine) this.input.value = item?.label || '';
  }

  render() {
    this.api = this.library.connect(this.machine.service, normalizeProps);
    spreadProps(this.root, this.api.getRootProps());
    spreadProps(this.label, this.api.getLabelProps());
    spreadProps(this.control, this.api.getControlProps());
    spreadProps(this.trigger, this.api.getTriggerProps());
    spreadProps(this.positioner, this.api.getPositionerProps());
    spreadProps(this.list, this.api.getContentProps());
    if (this.searchable) spreadProps(this.input, { ...this.api.getInputProps(), 'aria-label': this.labelText });
    else spreadProps(this.hiddenInput, this.api.getHiddenSelectProps());
    this.positioner.hidden = !this.api.open;
    for (const { item, row, title, check } of this.rows) {
      spreadProps(row, this.api.getItemProps({ item }));
      spreadProps(title, this.api.getItemTextProps({ item }));
      spreadProps(check, this.api.getItemIndicatorProps({ item }));
    }
    this.renderValue();
  }

  destroy() {
    this.unsubscribe?.();
    this.machine?.stop();
    this.positioner.remove();
    this.machine = null;
  }
}

// Controls inside the open panel act without selecting an option or closing the panel.
function keepOpen(control, action) {
  for (const type of ['pointerdown', 'pointerup', 'mousedown']) control.addEventListener(type, event => {
    event.preventDefault();
    event.stopPropagation();
  });
  control.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    action();
  });
}

export function mountPickers(container) {
  container.querySelectorAll('[data-picker-root]').forEach(root => pickers.get(root)?.mount());
}
export function disposePickers(container) {
  container.querySelectorAll('[data-picker-root]').forEach(root => pickers.get(root)?.destroy());
}
