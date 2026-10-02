import { $, el } from "./ui.js";

export function field(label, name, value = "", type = "input") {
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
  { confirm = "Save", submit = null, kicker = "EXPERIMENT" } = {},
) {
  $("#dialog-title").textContent = title;
  $("#dialog-content").replaceChildren(content);
  $("#dialog-kicker").textContent = kicker;
  $("#dialog-error").textContent = "";
  $("#confirm-dialog").textContent = confirm;
  $("#confirm-dialog").hidden = !submit;
  $("#cancel-dialog").textContent = submit ? "Cancel" : "Close";
  $("#confirm-dialog").disabled = false;
  $("#dialog-form").onsubmit = async (event) => {
    event.preventDefault();
    if (!submit) return;
    $("#confirm-dialog").disabled = true;
    try {
      await submit(new FormData($("#dialog-form")));
      $("#dialog").close();
    } catch (error) {
      $("#dialog-error").textContent = error.message;
    } finally {
      $("#confirm-dialog").disabled = false;
    }
  };
  $("#dialog").showModal();
}
$("#close-dialog").onclick = () => $("#dialog").close();
$("#cancel-dialog").onclick = () => $("#dialog").close();
