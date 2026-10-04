import { $, el } from "./ui.js";
import { Picker, mountPickers, disposePickers } from "./components.js?v=1";

export function field(label, name, value = "", type = "input") {
  if (type === 'select') {
    const input = new Picker({ id: 'field-'+name, name, label, value });
    const fragment = document.createDocumentFragment();
    fragment.append(input.root);
    return { fragment, input };
  }
  const fragment = document.createDocumentFragment(),
    id = "field-" + name;
  const caption = el("label", "", label);
  caption.htmlFor = id;
  const input = el(type);
  input.id = id;
  input.name = name;
  input.value = value;
  fragment.append(caption, input);
  return { fragment, input };
}

export function openDialog(
  title,
  content,
  { confirm = "Save", submit = null, kicker = "", pending = "Saving…", alternate = null } = {},
) {
  $("#dialog-title").textContent = title;
  disposePickers($("#dialog-content"));
  $("#dialog-content").replaceChildren(content);
  $("#dialog-kicker").textContent = kicker;
  $("#dialog-error").textContent = "";
  $("#confirm-dialog").textContent = confirm;
  $("#confirm-dialog").hidden = !submit;
  $("#cancel-dialog").textContent = submit ? "Cancel" : "Close";
  $("#confirm-dialog").disabled = false;
  $("#confirm-dialog").classList.remove('with-spinner');
  const alternateButton = $('#alternate-dialog');
  alternateButton.hidden = !alternate;
  alternateButton.textContent = alternate?.label || '';
  alternateButton.disabled = Boolean(alternate?.disabled);
  alternateButton.classList.remove('with-spinner');
  $('#confirm-dialog').classList.toggle('primary', !alternate);
  $("#dialog-form").setAttribute('aria-busy', 'false');
  let busy = false;
  const run = async (action, control, pendingText) => {
    if (!action || busy || !$('#dialog-form').reportValidity()) return;
    busy = true;
    const disabled = alternateButton.disabled;
    const label = control.textContent;
    for (const id of ['#confirm-dialog', '#alternate-dialog', '#cancel-dialog', '#close-dialog']) $(id).disabled = true;
    control.textContent = pendingText;
    control.classList.add('with-spinner');
    $("#dialog-form").setAttribute('aria-busy', 'true');
    try {
      await action(new FormData($("#dialog-form")));
      $("#dialog").close();
    } catch (error) {
      $("#dialog-error").textContent = error.message;
    } finally {
      busy = false;
      for (const id of ['#confirm-dialog', '#cancel-dialog', '#close-dialog']) $(id).disabled = false;
      alternateButton.disabled = disabled;
      control.textContent = label;
      control.classList.remove('with-spinner');
      $("#dialog-form").setAttribute('aria-busy', 'false');
    }
  };
  $('#dialog').oncancel = event => { if (busy) event.preventDefault(); };
  $('#dialog-form').onsubmit = event => { event.preventDefault(); return run(submit, $('#confirm-dialog'), pending); };
  alternateButton.onclick = () => run(alternate?.submit, alternateButton, alternate?.pending || 'Starting…');
  $("#dialog").showModal();
  mountPickers($("#dialog-content"));
  return { alternateButton };
}
$("#close-dialog").onclick = () => $("#dialog").close();
$("#cancel-dialog").onclick = () => $("#dialog").close();
