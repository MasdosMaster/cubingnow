import "flag-icons/css/flag-icons.min.css";

export function CountryFlag({ code = "" }) {
  if (!/^[a-z]{2}$/i.test(code)) return null;
  const countryCode = code.toLowerCase();
  const roundedClass = countryCode === "np" ? "" : " country-flag-rounded";
  return <span aria-hidden="true" className={`country-flag fi fi-${countryCode}${roundedClass}`} />;
}
