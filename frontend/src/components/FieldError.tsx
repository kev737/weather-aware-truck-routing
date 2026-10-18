/** A form field's error, under the field; screen readers hear it through aria-describedby. */
export function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <span className="field-error" id={id} role="alert">
      <span className="field-error-icon" aria-hidden="true">
        !
      </span>
      {message}
    </span>
  );
}
