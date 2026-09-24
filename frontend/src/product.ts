import productJson from '../../product.json';

/** Identidade do produto; a fonte única é o product.json da raiz do repositório. */
export const product: { name: string; slug: string; service_prefix: string } = productJson;
