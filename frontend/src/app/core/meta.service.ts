import { httpResource } from '@angular/common/http';
import { Injectable, computed } from '@angular/core';
import { apiUrl } from './api';
import { Meta } from './models';

/** Global platform metadata (target definition, disclaimers, configuration), fetched once. */
@Injectable({ providedIn: 'root' })
export class MetaService {
  readonly resource = httpResource<Meta>(() => apiUrl.meta());

  readonly meta = computed(() => (this.resource.hasValue() ? this.resource.value() : null));
  readonly target = computed(
    () =>
      this.meta()?.target ??
      'P(21-trading-day total return of stock > total return of its sector benchmark ETF), measured close(t) -> close(t+21)',
  );
}
